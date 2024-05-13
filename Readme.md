

## 本地模式
### 本地编译、安装

```
gem build fluent-plugin-mysql-binlog.gemspec

gem install ./fluent-plugin-mysql-binlog-0.1.0.gem
```


### 卸载
```
gem uninstall fluent-plugin-mysql-binlog -v 0.1.0
```

### 运行

* 打印到标准输出
```
fluentd -c ./conf/fluent.conf
```

## 镜像模式
### 镜像打包

#### 生成 fluentd-base:v1.16.2
```
docker build -f Dockerfile.base  . -t fluentd-base:v1.16.2
```

#### 基于 fluentd-base:v1.16.2

* 执行打包镜像
```
docker build -f Dockerfile  . -t fluentd-mysql-binlog:v1.16.2
```

### 镜像运行

```
sudo docker run -it --name=fluentBinlogSync  \
 --log-opt max-size=20m --log-opt max-file=1 \
 -v /etc/localtime:/etc/localtime \
 --privileged=true  \
 -v ./conf/fluent.binlog.conf:/fluentd/etc/fluent.conf \
 -v /var/log:/var/log \
 -v /var/log:/var/log \
 -d fluentd-mysql-binlog:v1.16.2
```

## 配置文件

* 配置文件模板如下
```
<source>
  @type mysql_binlog
  host MYSQL实例的IP
  port MYSQL实例的端口
  username MYSQL实例的访问用户名
  password MYSQL实例的访问密码(日志打印会将该字段做脱敏)
  database MYSQL实例要同步的DB名。可选。默认为空。
  table MYSQL实例要同步的表名,多张表用空格隔开，如-t tbl1 tbl2。可选。默认为空
  tag mysql_binlog
  interval 30
  only_dml true
  buffer_file_path /var/log/fluentd/mysql.binlog.in.buffer
</source>


<match ***>
  @type copy
  
  # 对比用，生产环境将该段去掉
  <store>
    @type stdout
  </store>

  <store>
    @type mysql_binlog
    host MYSQL实例的IP
    port MYSQL实例的端口
    username MYSQL实例的访问用户名
    password MYSQL实例的访问密码(日志打印会将该字段做脱敏)
    buffer_file_path /var/log/fluentd/mysql.binlog.out.buffer
 </store>
</match>

```

## FAQ

* binlog2sql.py 执行效率一般，可能和周期执行有关，对积压的大量 binlog 处理不得力

* 目前不支持同步已有数据，只是按照增量形式补偿，因此并不含初次使用场景经常遇到的原有数据建立

* 并不确定，在binlog文件变迁情况下，表现如何

* 和mysql的binlog同步机制搭配上，有多线程执行时会遇到 server_id 冲突问题。

* 关联关系：插件注册名称/配置文件type名 - 代码文件名称
```
    代码文件名称 = 插件类型(in/out)_插件注册名称/配置文件type名
    
    插件注册名称/配置文件type名：不能用中划线，可用下划线
```

* 镜像中，本插件的运行目录为
```
/usr/local/bundle/gems/[本插件]
```

* 若有多个在运行时，会遇到该报错，来源是本程序向 mysql master 注册自身 slave身份时，slave_id 冲突，暂无解决方法
```
Traceback (most recent call last):
        File "./binlog2sql/binlog2sql/binlog2sql.py", line 150, in <module>
    binlog2sql.process_binlog()
    File "./binlog2sql/binlog2sql/binlog2sql.py", line 74, in process_binlog
    for binlog_event in stream:
    File "/usr/local/lib/python3.8/dist-packages/pymysqlreplication/binlogstream.py", line 430, in fetchone
    pkt = self._stream_connection._read_packet()
    File "/usr/local/lib/python3.8/dist-packages/pymysql/connections.py", line 684, in _read_packet
    packet.check_error()
    File "/usr/local/lib/python3.8/dist-packages/pymysql/protocol.py", line 220, in check_error
    err.raise_mysql_exception(self._data)
    File "/usr/local/lib/python3.8/dist-packages/pymysql/err.py", line 109, in raise_mysql_exception
    raise errorclass(errno, errval)
    pymysql.err.InternalError: (1236, "A replica with the same server_uuid/server_id as this replica has connected to the source; the first event 'binlog.000017' at 4, the last event read from './binlog.000017' at 96475, the last byte read from './binlog.000017' at 96475.")
```


* [TODO：可能需优化] 当前一次性读入的长度没有限制，不确定是否会出现首次读的时候 binlog 过大，导致内存过大


* binlog文件太多时，mysql的连接数需调整(当然，生产环境业务需要，该值本就应该调整)
操作如：
```
mysql> show variables like 'max_connections';
+-----------------+-------+
| Variable_name   | Value |
+-----------------+-------+
| max_connections | 151   |
+-----------------+-------+
1 row in set

mysql> set global max_connections=1000 ;

        mysql> show variables like 'max_connections';
        +-----------------+-------+
        | Variable_name   | Value |
        +-----------------+-------+
        | max_connections | 1000  |
        +-----------------+-------+
        1 row in set
```

* Mysql 相关权限
以下命令，需要 Mysql Client 具有 privilege 权限
```
SHOW BINARY LOGS
```
[参看](https://dev.mysql.com/doc/refman/8.3/en/show-binary-logs.html)




### Mysql版本差异

* SHOW BINARY LOG STATUS 和 SHOW MASTER STATUS
```
SHOW BINARY LOG STATUS 
```
MySQL 8.2.0 引入，替代
```
SHOW MASTER STATUS
```
[参看](https://dev.mysql.com/doc/refman/8.3/en/show-binary-log-status.html)