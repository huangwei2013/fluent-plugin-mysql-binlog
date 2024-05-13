require 'fluent/plugin/output'
require 'mysql2'

module Fluent::Plugin
  class MysqlOutBinlogOutput < Output
    Fluent::Plugin.register_output('mysql_binlog', self)
    config_param :host, :string, :default => 'localhost'
    config_param :port, :string, :default => 3306
    config_param :username, :string, :default => 'root'
    config_param :password, :string, :default => nil, secret: true
    config_param :tag, :string, :default => 'mysql_binlog_out'
    config_param :buffer_file_path, :string, :default => '/var/log/fluentd/mysql_binlog.out.buffer'

    def configure(conf)
      super
      @binlog_files = {}
    end

    def start
      super
      @client = Mysql2::Client.new(host: @host, port: @port, username: @username, password: @password)
    end

    def shutdown
      super
      @client.close if @client
    end

    def process(tag, es)
      es.each do |time, record|

        # 若 output 执行先于 input，则会读到一些非法格式内容，这里做个过滤
        if !record['sql'].nil?
          sql = record['sql']
          binlog_file = record['binlog_file']
          offset = record['offset']

          execute_sql(sql)

          # 将 offset 添加到对应 binlog_file 的数组中
          @binlog_files[binlog_file] = offset
        end
      end

      # 将每个 binlog_file 及其对应的 offset 记录到缓冲文件中
      write_binlog_offsets_to_buffer_file

    end


    private

    def execute_sql(sql)
      begin
        @client.query(sql)
      rescue StandardError => e
        log.error "Error executing SQL query: #{e.message}"
      end
    end

    def write_binlog_offsets_to_buffer_file
      File.open(@buffer_file_path, 'w') do |file|
        @binlog_files.each do |binlog_file, offset|
          file.puts "#{binlog_file}=#{offset}"
        end
      end
    end

  end
end
