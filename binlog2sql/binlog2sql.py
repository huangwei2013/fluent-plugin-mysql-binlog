#!/usr/bin/env python
# -*- coding: utf-8 -*-
import re
import sys
import datetime
import pymysql
import os
from pymysqlreplication import BinLogStreamReader
from pymysqlreplication.event import QueryEvent, RotateEvent, FormatDescriptionEvent, GtidEvent
from utils.binlog2sql_util import command_line_args, concat_sql_from_binlog_event, is_dml_event, event_type, logger, \
    set_log_format, get_gtid_set, is_want_gtid, save_result_sql, dt_now, handle_rollback_sql, get_max_gtid, \
    remove_max_gtid, connect2sync_mysql
from utils.other_utils import create_unique_file, temp_open, split_condition, merge_rename_args

sep = '/' if '/' in sys.argv[0] else os.sep


class Binlog2sql(object):

    def __init__(self, connection_settings, start_file=None, start_pos=None, end_file=None, end_pos=None,
                 start_time=None, stop_time=None, only_schemas=None, only_tables=None, no_pk=False,
                 flashback=False, stop_never=False, only_dml=True, sql_type=None, rename_tb=None,
                 need_comment=1, rename_db=None, only_pk=False, ignore_databases=None, ignore_tables=None,
                 ignore_columns=None, replace=False, insert_ignore=False, remove_not_update_col=False,
                 result_file=None, result_dir=None, table_per_file=False, date_prefix=False,
                 include_gtids=None, exclude_gtids=None, update_to_replace=False, keep_not_update_col: list = None,
                 chunk_size=1000, tmp_dir='tmp', no_date=False, where=None, args=None):
        """
        conn_setting: {'host': 127.0.0.1, 'port': 3306, 'user': user, 'passwd': passwd, 'charset': 'utf8'}
        """

        if not start_file:
            raise ValueError('Lack of parameter: start_file')

        self.conn_setting = connection_settings
        self.start_file = start_file
        self.start_pos = start_pos if start_pos else 4  # use binlog v4
        self.end_file = end_file if end_file else start_file
        self.end_pos = end_pos
        if start_time:
            self.start_time = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        else:
            self.start_time = datetime.datetime.strptime('1980-01-01 00:00:00', "%Y-%m-%d %H:%M:%S")
        if stop_time:
            self.stop_time = datetime.datetime.strptime(stop_time, "%Y-%m-%d %H:%M:%S")
        else:
            self.stop_time = datetime.datetime.strptime('2999-12-31 00:00:00', "%Y-%m-%d %H:%M:%S")

        self.only_schemas = only_schemas if only_schemas else None
        self.only_tables = only_tables if only_tables else None
        self.no_pk, self.flashback, self.stop_never = (no_pk, flashback, stop_never)
        self.only_dml = only_dml
        self.sql_type = [t.upper() for t in sql_type] if sql_type else []

        self.binlogList = []
        self.connection = pymysql.connect(**self.conn_setting)
        self.need_comment = need_comment
        self.rename_db_dict = merge_rename_args(rename_db) if rename_db else dict()
        self.rename_tb_dict = merge_rename_args(rename_tb) if rename_tb else dict()
        self.only_pk = only_pk
        self.ignore_databases = ignore_databases
        self.ignore_tables = ignore_tables
        self.ignore_columns = ignore_columns if ignore_columns is not None else []
        self.replace = replace
        self.insert_ignore = insert_ignore
        self.remove_not_update_col = remove_not_update_col
        self.result_file = result_file
        self.result_dir = result_dir
        self.table_per_file = table_per_file
        self.date_prefix = date_prefix
        self.gtid_set = get_gtid_set(include_gtids, exclude_gtids)
        self.gtid_max_dict = get_max_gtid(self.gtid_set.get('include', {}))
        self.update_to_replace = update_to_replace
        self.keep_not_update_col = keep_not_update_col if keep_not_update_col is not None else []
        self.no_date = no_date
        self.f_result_sql_file = ''
        self.chunk_size = chunk_size
        self.tmp_dir = tmp_dir
        if not os.path.exists(tmp_dir):
            os.makedirs(tmp_dir, exist_ok=True)

        self.filter_conditions = split_condition(where) if where is not None else []
        if remove_not_update_col and self.filter_conditions:
            for cond_elem in self.filter_conditions:
                if isinstance(cond_elem, dict):
                    cond_column = cond_elem['column']
                    if cond_column not in self.keep_not_update_col and cond_column not in self.ignore_columns:
                        self.keep_not_update_col.append(cond_column)
                elif isinstance(cond_elem, tuple):
                    for cond in cond_elem:
                        cond_column = cond['column']
                        if cond_column not in self.keep_not_update_col and cond_column not in self.ignore_columns:
                            self.keep_not_update_col.append(cond_column)

        self.args = args
        if args.sync:
            self.rename_db_dict = {"*": args.sync_database}
        if self.rename_db_dict and not self.only_dml:
            logger.error(f'args --rename-db only work with DML SQL. '
                         f'We suggest you add --only-dml args.')
            choice = input('Do you want to add --only-dml args? [y]/n: ')
            if choice in ['y', '']:
                self.only_dml = True


        # Note:根据不同mysql版本，使用不同的方法获取 binary log files
        # 参看：https://dev.mysql.com/doc/refman/8.3/en/show-binary-log-status.html
        cursor = self.connection.cursor()
        cursor.execute("SELECT VERSION()>'8.2.0'")
        cur_record = cursor.fetchone()
        mysql_version_flag = cur_record[0]
        SQL_BINLOG_STATUS = SQL_BINLOG_FILES = ""
        if mysql_version_flag == 0:
            SQL_BINLOG_STATUS = "SHOW MASTER STATUS"
            SQL_BINLOG_FILES = "SHOW MASTER LOGS"
        else:
            SQL_BINLOG_STATUS = "SHOW BINARY LOG STATUS"
            SQL_BINLOG_FILES = "SHOW BINARY LOGS"

        with self.connection.cursor() as cursor:
            cursor.execute(SQL_BINLOG_STATUS)
            self.eof_file, self.eof_pos = cursor.fetchone()[:2]
            cursor.execute(SQL_BINLOG_FILES)
            bin_index = [row[0] for row in cursor.fetchall()]
            if self.start_file not in bin_index:
                raise ValueError('parameter error: start_file %s not in mysql server' % self.start_file)
            binlog2i = lambda x: x.split('.')[1]
            for binary in bin_index:
                if binlog2i(self.start_file) <= binlog2i(binary) <= binlog2i(self.end_file):
                    self.binlogList.append(binary)

            cursor.execute("SELECT @@server_id")
            self.server_id = cursor.fetchone()[0]
            if not self.server_id:
                raise ValueError('missing server_id in %s:%s' % (self.conn_setting['host'], self.conn_setting['port']))

    def process_binlog(self):
        stream = BinLogStreamReader(connection_settings=self.conn_setting, server_id=self.server_id,
                                    log_file=self.start_file, log_pos=self.start_pos, only_schemas=self.only_schemas,
                                    only_tables=self.only_tables, resume_stream=True, blocking=True,
                                    ignored_schemas=self.ignore_databases, ignored_tables=self.ignore_tables)
        mode = 'w'
        if self.result_file:
            result_sql_file = self.result_file
            logger.info(f'Saving result into file: [{result_sql_file}]')
            self.f_result_sql_file = open(result_sql_file, mode)
        elif self.table_per_file:
            logger.info(f'Saving table per file into dir: [{self.result_dir}]')

        binlog_gtid = ''
        gtid_set = True if self.gtid_set else False
        flag_last_event = False
        e_start_pos, last_pos = stream.log_pos, stream.log_pos
        tmp_file = create_unique_file('%s.%s' % (self.conn_setting['host'], self.conn_setting['port']))
        tmp_file = os.path.join(self.tmp_dir, tmp_file)
        flashback_warn_flag = 1

        sync_conn = ''
        sync_cursor = ''
        with temp_open(tmp_file, "w") as f_tmp, self.connection.cursor() as cursor:
            if self.args and self.args.sync:
                sync_conn = connect2sync_mysql(self.args)
                sync_cursor = sync_conn.cursor()
            for binlog_event in stream:
                # 返回的 EVENT 顺序
                # RotateEvent
                # FormatDescriptionEvent
                # GtidEvent
                # QueryEvent
                # TableMapEvent
                # UpdateRowsEvent
                # XidEvent
                # GtidEvent
                # QueryEvent
                # TableMapEvent
                # UpdateRowsEvent
                # XidEvent
                # GtidEvent
                # ...
                if not self.stop_never:
                    try:
                        event_time = datetime.datetime.fromtimestamp(binlog_event.timestamp)
                    except OSError:
                        event_time = datetime.datetime(1980, 1, 1, 0, 0)
                    if (stream.log_file == self.end_file and stream.log_pos == self.end_pos) or \
                            (stream.log_file == self.eof_file and stream.log_pos == self.eof_pos):
                        flag_last_event = True
                    elif event_time < self.start_time:
                        if not (isinstance(binlog_event, RotateEvent)
                                or isinstance(binlog_event, FormatDescriptionEvent)):
                            last_pos = binlog_event.packet.log_pos
                        continue
                    elif (stream.log_file not in self.binlogList) or \
                            (self.end_pos and stream.log_file == self.end_file and stream.log_pos > self.end_pos) or \
                            (stream.log_file == self.eof_file and stream.log_pos > self.eof_pos) or \
                            (event_time >= self.stop_time):
                        break
                    # else:
                    #     raise ValueError('unknown binlog file or position')

                if isinstance(binlog_event, QueryEvent) and binlog_event.query == 'BEGIN':
                    e_start_pos = last_pos

                if isinstance(binlog_event, GtidEvent):
                    binlog_gtid = str(binlog_event.gtid)
                    if self.gtid_max_dict:
                        remove_max_gtid(self.gtid_max_dict, binlog_gtid)
                        if not self.gtid_max_dict:
                            logger.info('The parse process exited because the gtid condition reached the maximum '
                                        'value, or may be you give a invalid gtid sets to args --include-gtid')
                            break

                if isinstance(binlog_event, QueryEvent) and not self.only_dml:
                    if binlog_gtid and gtid_set and not is_want_gtid(self.gtid_set, binlog_gtid):
                        continue

                    sql, db, table = concat_sql_from_binlog_event(
                        cursor=cursor, binlog_event=binlog_event, only_return_sql=False,
                        flashback=self.flashback, no_pk=self.no_pk, rename_db_dict=self.rename_db_dict, only_pk=self.only_pk,
                        ignore_columns=self.ignore_columns, replace=self.replace, insert_ignore=self.insert_ignore,
                        remove_not_update_col=self.remove_not_update_col, binlog_gtid=binlog_gtid,
                        update_to_replace=self.update_to_replace, keep_not_update_col=self.keep_not_update_col,
                        filter_conditions=self.filter_conditions, rename_tb_dict=self.rename_tb_dict,
                    )
                    if sql:
                        if self.need_comment != 1:
                            sql = re.sub('; #.*', ';', sql)

                        if not self.flashback:
                            if self.f_result_sql_file:
                                self.f_result_sql_file.write(sql + '\n')
                            elif self.table_per_file:
                                if db and table:
                                    if self.date_prefix:
                                        filename = f'{dt_now()}.' + db + '.' + table + '.sql'
                                    elif self.no_date:
                                        filename = db + '.' + table + '.sql'
                                    else:
                                        filename = db + '.' + table + f'.{dt_now()}.sql'
                                else:
                                    if self.date_prefix:
                                        filename = f'{dt_now()}.others.sql'
                                    elif self.no_date:
                                        filename = f'others.sql'
                                    else:
                                        filename = f'others.{dt_now()}.sql'
                                result_sql_file = os.path.join(self.result_dir, filename)
                                save_result_sql(result_sql_file, sql + '\n')
                            elif sync_cursor:
                                sync_conn.ping(reconnect=True)
                                if re.match('USE .*;\n', sql) is not None:
                                    sql = re.sub('USE .*;\n', '', sql)
                                try:
                                    sync_cursor.execute(sql)
                                except:
                                    logger.exception(f'Could not execute sql: {sql}')
                                    logger.error(
                                        f'Exit at binlog file {stream.log_file} '
                                        f'start pos {e_start_pos} end pos {binlog_event.packet.log_pos}'
                                    )
                                    break
                            else:
                                print(sql)
                        else:
                            if flashback_warn_flag == 1:
                                logger.warning(f'Saving the result into the temp file, please wait until the parsing '
                                               f'process is done, then reverse the order of results to you.')
                                flashback_warn_flag = 0
                            f_tmp.write(sql + '\n')
                elif is_dml_event(binlog_event) and event_type(binlog_event) in self.sql_type:
                    exit_flag = 0
                    for row in binlog_event.rows:
                        if binlog_gtid and gtid_set and not is_want_gtid(self.gtid_set, binlog_gtid):
                            continue

                        sql, db, table = concat_sql_from_binlog_event(
                            cursor=cursor, binlog_event=binlog_event, no_pk=self.no_pk, row=row,
                            flashback=self.flashback, e_start_pos=e_start_pos, rename_db_dict=self.rename_db_dict,
                            only_pk=self.only_pk, ignore_columns=self.ignore_columns, replace=self.replace,
                            insert_ignore=self.insert_ignore, remove_not_update_col=self.remove_not_update_col,
                            only_return_sql=False, binlog_gtid=binlog_gtid, update_to_replace=self.update_to_replace,
                            keep_not_update_col=self.keep_not_update_col, filter_conditions=self.filter_conditions,
                            rename_tb_dict=self.rename_tb_dict,
                        )
                        try:
                            if sql:
                                if self.need_comment != 1:
                                    sql = re.sub('; #.*', ';', sql)

                                if not self.flashback:
                                    if self.f_result_sql_file:
                                        self.f_result_sql_file.write(sql + '\n')
                                    elif self.table_per_file:
                                        if db and table:
                                            if self.date_prefix:
                                                filename = f'{dt_now()}.' + db + '.' + table + '.sql'
                                            elif self.no_date:
                                                filename = db + '.' + table + '.sql'
                                            else:
                                                filename = db + '.' + table + f'.{dt_now()}.sql'
                                        else:
                                            if self.date_prefix:
                                                filename = f'{dt_now()}.others.sql'
                                            elif self.no_date:
                                                filename = f'others.sql'
                                            else:
                                                filename = f'others.{dt_now()}.sql'
                                        result_sql_file = os.path.join(self.result_dir, filename)
                                        save_result_sql(result_sql_file, sql + '\n')
                                    elif sync_cursor:
                                        sync_conn.ping(reconnect=True)
                                        try:
                                            sync_cursor.execute(sql)
                                        except:
                                            logger.exception(f'Could not execute sql: {sql}')
                                            logger.error(
                                                f'Exit at binlog file {stream.log_file} '
                                                f'start pos {e_start_pos} end pos {binlog_event.packet.log_pos}'
                                            )
                                            exit_flag = 1
                                            break
                                    else:
                                        print(sql)
                                else:
                                    if flashback_warn_flag == 1:
                                        logger.warning(
                                            f'Saving the result into the temp file, please wait until the parsing '
                                            f'process is done, then reverse the order of results to you.')
                                        flashback_warn_flag = 0
                                    f_tmp.write(sql + '\n')
                        except Exception:
                            logger.exception('')
                            logger.error('Error sql: %s' % sql)
                            continue

                    if exit_flag == 1:
                        break

                if not (isinstance(binlog_event, RotateEvent) or isinstance(binlog_event, FormatDescriptionEvent)):
                    last_pos = binlog_event.packet.log_pos
                if flag_last_event:
                    break

            stream.close()
            f_tmp.close()
            if self.f_result_sql_file:
                self.f_result_sql_file.close()

            if self.flashback:
                handle_rollback_sql(self.f_result_sql_file, self.table_per_file, self.date_prefix, self.no_date,
                                    self.result_dir, tmp_file, self.chunk_size, self.tmp_dir, self.result_file,
                                    sync_conn, sync_cursor)

            if sync_cursor:
                sync_cursor.close()
                sync_conn.close()
        return True

    def __del__(self):
        pass


def main(args):
    conn_setting = {
        'host': args.host,
        'port': args.port,
        'user': args.user,
        'passwd': args.password,
        'charset': 'utf8mb4'
    }

    binlog2sql = Binlog2sql(
        connection_settings=conn_setting, start_file=args.start_file, start_pos=args.start_pos,
        end_file=args.end_file, end_pos=args.end_pos, start_time=args.start_time,
        stop_time=args.stop_time, only_schemas=args.databases, only_tables=args.tables,
        no_pk=args.no_pk, flashback=args.flashback, stop_never=args.stop_never,
        only_dml=args.only_dml, sql_type=args.sql_type, no_date=args.no_date,
        need_comment=args.need_comment, rename_db=args.rename_db, only_pk=args.only_pk, rename_tb=args.rename_tb,
        ignore_databases=args.ignore_databases, ignore_tables=args.ignore_tables,
        ignore_columns=args.ignore_columns, replace=args.replace, insert_ignore=args.insert_ignore,
        remove_not_update_col=args.remove_not_update_col, table_per_file=args.table_per_file,
        result_file=args.result_file, result_dir=args.result_dir, date_prefix=args.date_prefix, args=args,
        include_gtids=args.include_gtids, exclude_gtids=args.exclude_gtids, update_to_replace=args.update_to_replace,
        keep_not_update_col=args.keep_not_update_col, chunk_size=args.chunk, tmp_dir=args.tmp_dir, where=args.where,
    )
    binlog2sql.process_binlog()


if __name__ == '__main__':
    command_line_args = command_line_args(sys.argv[1:])
    set_log_format()
    main(command_line_args)
