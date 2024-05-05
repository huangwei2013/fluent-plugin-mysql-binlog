

基于(v2) [https://github.com/michael-liumh/binlog2sql](https://github.com/michael-liumh/binlog2sql)

但 v2 版本在处理 mysql 表的 JSON 类型字段有问题， 需要升级 pymysql 和 python-mysql-replication，又因为升级版本差异，需要改动 binlog2sql 的代码



```
 pip3 install --upgrade --force-reinstall mysql-replication==0.45.1
 pip3 install --upgrade --force-reinstall PyMySQL==1.1.0
 pip3 install --upgrade --force-reinstall chardet==3.0.4
 pip3 install --upgrade --force-reinstall colorlog==5.0.1
 pip3 install --upgrade --force-reinstall rich==12.2.0
 pip3 install --upgrade --force-reinstall cryptography==36.0.1
```

* 随版本的代码变更
```
self.connection 
	对 cursor 的赋值，改成
self.connection.cursor()
```


PyMySQL==1.1.0
wheel==0.29.0
mysql-replication==0.45.1
colorlog==5.0.1
chardet==3.0.4
cryptography==36.0.1
rich==12.2.0