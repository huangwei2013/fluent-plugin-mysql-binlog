require 'fluent/plugin/input'
require 'open3'
require 'mysql2'

module Fluent::Plugin
  class MysqlInBinlogInput < Input
    Fluent::Plugin.register_input('mysql_binlog', self)

    config_param :host, :string, :default => 'localhost'
    config_param :port, :string, :default => 3306
    config_param :username, :string, :default => 'root'
    config_param :password, :string, :default => nil, secret: true
    config_param :database, :string, :default => nil
    config_param :table, :string, :default => nil
    config_param :tag, :string, :default => 'mysql_binlog_in'
    config_param :interval, :integer, :default => 30
    config_param :buffer_file_path, :string, :default => '/var/log/fluentd/mysql_binlog.in.buffer'

    config_param :only_dml, :bool, :default => true

    def initialize
      super
    end

    def configure(conf)
      super
      @binlog_files = {}
      @binlog_files_update_flag = false
    end

   def start
      super

      load_binlog_offsets_from_buffer_file

      @threads = {}
      fetch_start_files.each do |log_file, offset|

        if @binlog_files[log_file].nil?
            offset = 0
            @binlog_files[log_file] = offset
        else
            offset = @binlog_files[log_file]
        end

        puts "run #{log_file} #{offset}"
        @threads[log_file] = Thread.new do
          begin
            run("#{log_file}", "#{offset}")
          rescue => e
            $log.error "运行 run 方法时出错：#{e.message}"
          end
        end
      end

      @buffer_thread = Thread.new { run_buffer_thread }
    end

    def shutdown
      super
      @threads.each_value(&:terminate)
      @threads.each_value(&:join)

      @binlog_files_update_flag = true
      write_binlog_offsets_to_buffer_file
    end

    private

    def run_buffer_thread
      loop do
        sleep 60 # 每分钟执行一次写入操作
        write_binlog_offsets_to_buffer_file
      end
    end

    def write_binlog_offsets_to_buffer_file

      if @binlog_files_update_flag
        # 将多行 key=value 内容写入到 buffer 文件中
        puts "binlog_files : #{@binlog_files}"
        File.open(@buffer_file_path, 'w') do |file|
          @binlog_files.each do |binlog_file, offset|
            file.puts "#{binlog_file}=#{offset}"
          end
        end
      end
    end

    def load_binlog_offsets_from_buffer_file
      # 从 buffer 文件中读取先前同步的多行内容
      # 每一行的格式为 key=value，其中 key 是 binlog 文件名，value 是已同步的 offset 值
      File.foreach(@buffer_file_path ) do |line|
        key, value = line.chomp.split('=')
        @binlog_files[key] = value.to_i
      end

      rescue Errno::ENOENT
        log.warn "Buffer file '#{@buffer_file_path}' not found. Starting with empty offsets."
      {}
    end

    def getRunCommand(log_file, offset)
      cur_run_path = File.dirname(__FILE__)
      script="#{cur_run_path}/../../../binlog2sql/binlog2sql.py"
      command = "#{script} -h #{@host} -P #{@port} -u #{@username}  -p #{@password} --start-file=#{log_file} --start-pos=#{offset} "

      if !@database.nil? && !@database.empty?
          command += " -d #{@database} "
      end

      if !@table.nil? && !@table.empty?
          command += " -t #{@table} "
      end

      if @only_dml
          command += "  --only-dml "
      end

      return  command
    end

    def run(log_file, offset)

      loop do

        if !@binlog_files[log_file].nil?
            offset = @binlog_files[log_file]
        else
            @binlog_files[log_file] = offset
        end

        command = getRunCommand(log_file, offset)
        stdout, stderr, status = Open3.capture3("#{command}") # 获取标准输出和标准错误

        # 检查命令是否成功执行
        if !status.success?
          puts "执行失败：#{status}"
        end
        stdout.each_line do |line|

          begin
            line_statement = line.chomp
            parts = line_statement.split("; #start")
            next if parts.length == 1 # 如果没有匹配到，parts 长度应该为 1

            sql = parts[0]
            offset = parts[1].match(/end (\d+)/)[1].to_i

            # 发送 SQL 语句到 Fluentd 流
            router.emit(@tag, Fluent::Engine.now, {'sql' => sql, 'binlog_file'=>log_file, 'offset' => offset})
         rescue => e
           log.error("Error processing binlog line: #{e.message}")
         end

        end

        # 有变化才更新
        if offset > @binlog_files[log_file]
          @binlog_files[log_file] = offset
          @binlog_files_update_flag = true
        end

        sleep @interval  # 同步周期
      end
    end

    def fetch_start_files
      client = Mysql2::Client.new(host: @host, port: @port, username: @username, password: @password)

      # 执行 SQL 查询获取 binlog 文件列表及对应的偏移量
      result = client.query("SHOW BINARY LOGS")

      # 解析查询结果获取 binlog 文件列表及对应的偏移量
      start_files = {}
      result.each do |row|
        start_files[row['Log_name']] = 0
      end

      # 返回 binlog 文件列表及对应的偏移量
      start_files
    ensure
      client.close if client
    end
  end
end
