# fluent-plugin-mysql-binlog.gemspec

Gem::Specification.new do |spec|
  spec.name          = "fluent-plugin-mysql-binlog"
  spec.version       = "0.1.0"
  spec.authors       = ["Your Name"]
  spec.email         = ["your.email@example.com"]
  spec.summary       = %q{Fluentd plugin for parsing MySQL with python binlog}
  spec.homepage      = "https://github.com/your_username/fluent-plugin-mysql-binlog"
  spec.license       = "MIT"

  spec.add_runtime_dependency 'fluentd', '>= 1.0', '< 3.0'
  spec.files         = Dir.glob("lib/**/*.rb") + ["fluent-plugin-mysql-binlog.gemspec"]
  spec.files         += Dir.glob("binlog2sql/**/*")end
