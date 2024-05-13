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

      @binlog_files = {}
      @binlog_files_update_flag = false

	  @mutex_binlog_list = Mutex.new

      @mutex_binlog_sync = Mutex.new
      @mysql_sync_safe_flag = nil
    end

    def configure(conf)
      super

      if @interval == 0
        @interval = 30
      end
    end

   def start
      super

      load_binlog_offsets_from_buffer_file

      @threads = {}

      @buffer_thread_1 = Thread.new { run_sync }
      @buffer_thread_2 = Thread.new { run_sync_info_save }
      @buffer_thread_3 = Thread.new { run_merge_binlog_files }
    end


    def shutdown
      super
      @threads.each_value(&:terminate)
      @threads.each_value(&:join)

      @binlog_files_update_flag = true
      write_binlog_offsets_to_buffer_file
    end


    private


    def run_sync
      sleep 1
      loop do
        @binlog_files.each do |log_file, v|
          if @binlog_files[log_file]["sync_flag"] == 1
            if @binlog_files[log_file]["thread_id"].nil? || (@binlog_files[log_file]["thread_id"] == 0)
              @threads[log_file] = Thread.new do
                begin
                  run_binlog_sync(log_file)
                rescue => e
                  log.error "run_binlog_sync fail : #{e.message}"
                end
              end
            end
          end
        end
        sleep interval
      end
    end


    def run_merge_binlog_files
      loop do
        merge_binlog_files
        sleep 30
      end
    end


    def run_sync_info_save
      loop do
        sleep 60 
        write_binlog_offsets_to_buffer_file
      end
    end


    def merge_binlog_files
      current_timestamp = Time.now.to_i
      fetch_sync_files.each do |log_file, offset_binlog|
        if @binlog_files[log_file].nil?
          offset_sync = 0
          @binlog_files[log_file] = { "log_file"=>log_file, "offset_sync"=>0, "offset_binlog"=>offset_binlog, "sync_flag"=>1, "sync_time"=>current_timestamp }
          log.info("new binlog file : #{log_file}")
        else
          offset_sync = @binlog_files[log_file]["offset_sync"]
          @binlog_files[log_file]["sync_time"] = current_timestamp
          @binlog_files[log_file]["offset_binlog"] = offset_binlog
        end

        if (offset_binlog > 0)
          if offset_binlog > offset_sync
            @binlog_files[log_file]["sync_flag"] = 1
          else
            @binlog_files[log_file]["sync_flag"] = 0
          end
        end
      end

      @binlog_files.each do |log_file, v|
        if @binlog_files[log_file]["sync_time"] != current_timestamp
          @binlog_files[log_file]["sync_flag"] = 0
        end
      end
    end


    def write_binlog_offsets_to_buffer_file
      if @binlog_files_update_flag
        if File.exist?(@buffer_file_path)
          FileUtils.cp(@buffer_file_path, "#{@buffer_file_path}.bak")
        end

        File.open(@buffer_file_path, 'w') do |file|
          @binlog_files.each do |binlog_file, valueInJson|
            file.puts "#{binlog_file}=#{valueInJson["offset_sync"]}"
          end
        end
        @binlog_files_update_flag = false
      end
    end


    def load_binlog_offsets_from_buffer_file
      begin
        # each line: key=value
        File.foreach(@buffer_file_path) do |line|
          key, value = line.chomp.split('=')
          @binlog_files[key] = { "log_file"=>key, "offset_sync"=>value.to_i, "offset_binlog"=>0, "sync_flag"=>0, "sync_time"=>0}
        end
      rescue Errno::ENOENT
        log.warn "Buffer file '#{@buffer_file_path}' not found. Starting with empty offsets."
      end
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


    def safe_lock(mutex_value)
      ret = nil 
      if @mutex_binlog_sync.try_lock
        begin
          if @mysql_sync_safe_flag.nil?
            @mysql_sync_safe_flag = mutex_value
            ret = mutex_value     
          elsif @mysql_sync_safe_flag == mutex_value
            ret = mutex_value  
          end
        ensure
          @mutex_binlog_sync.unlock
        end
      end
      return ret
    end


  
    def safe_unlock(mutex_value)
      ret = nil 
      if @mutex_binlog_sync.try_lock
        begin
          if @mysql_sync_safe_flag.nil?
            ret = mutex_value    
          elsif @mysql_sync_safe_flag == mutex_value
            @mysql_sync_safe_flag = nil
            ret = mutex_value   
          end
        ensure
          @mutex_binlog_sync.unlock
        end
      end
      return ret
    end


    def run_binlog_sync(log_file)
      thread_id = Thread.current.object_id
      @binlog_files[log_file]["thread_id"] = thread_id
      offset_sync = @binlog_files[log_file]["offset_sync"]
      offset_binlog = @binlog_files[log_file]["offset_binlog"]

      log.info("[sync start] log_file:#{log_file}, offset_sync:#{offset_sync}, offset_binlog:#{offset_binlog}, thread_id:#{thread_id}")

      loop do
        if !@binlog_files[log_file].nil?
            offset_sync = @binlog_files[log_file]["offset_sync"]
        else
            @binlog_files[log_file]["offset_sync"] = offset_sync
        end

        command = getRunCommand(log_file, offset_sync)
        sync_counter = -1

        begin
          try_lock = safe_lock(thread_id)
          if !try_lock.nil?
            log.info(" thread : #{thread_id}, binlog : #{log_file}, run : #{command}")

            sync_counter = 0
            stdout = IO.popen("#{command}")
            stdout.each_line do |line|
              line_statement = line.chomp
              parts = line_statement.split("; #start")
              next if parts.length == 1

              sql = parts[0]
              offset_sync = parts[1].match(/end (\d+)/)[1].to_i

              router.emit(@tag, Fluent::Engine.now, {'sql' => sql, 'binlog_file'=>log_file, 'offset' => offset_sync})
              sync_counter += 1

              if offset_sync > @binlog_files[log_file]["offset_sync"]
                @binlog_files[log_file]["offset_sync"] = offset_sync
                @binlog_files_update_flag = true
              end
            end
          end
        rescue => e
          log.error("Error processing binlog #{log_file} : #{e.message}")
        ensure
          safe_unlock(thread_id)
        end

        if sync_counter == 0
          @binlog_files[log_file]["offset_sync"] = offset_binlog
          @binlog_files_update_flag = true
          @binlog_files[log_file]["sync_flag"] = 0
        end

        if @binlog_files[log_file]["sync_flag"] == 1
          sleep @interval
        else
          log.info("[run sync stop] log_file:#{log_file}, offset_sync:#{offset_sync}, offset_binlog:#{offset_binlog}, thread_id:#{thread_id}")
          @binlog_files[log_file]["thread_id"] = 0
          break
        end
      end
    end


    def fetch_sync_files
      cur_binlog_files = {}
      client = Mysql2::Client.new(host: @host, port: @port, username: @username, password: @password)
      result = client.query("SHOW BINARY LOGS")
      result.each do |row|
        cur_binlog_files[row['Log_name']] = row['File_size']
      end
      return cur_binlog_files
      
      ensure
        client.close if client
      end
    end
end
