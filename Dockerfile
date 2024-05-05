FROM fluentd-base:v1.16.2
USER root

RUN apt-get update \
    && apt-get -f install \
    && apt-get install -y --fix-broken \
    && apt-get autoclean \
    && apt-get install  --allow-downgrades -y libbz2-1.0=1.0.8-2  \
    && apt-get install --allow-downgrades -y perl-base=5.30.0-9ubuntu0.5 \
    && apt-get install -y build-essential \
    && apt-get install -y mysql-client \
    && apt-get install -y libmysqlclient-dev \
    && gem install open3 \
    && gem install mysql2 \
    && pip3 install  mysql-replication==0.45.1 -i https://pypi.tuna.tsinghua.edu.cn/simple  \
    && pip3 install  PyMySQL==1.1.0 -i https://pypi.tuna.tsinghua.edu.cn/simple  \
    && pip3 install  chardet==3.0.4 -i https://pypi.tuna.tsinghua.edu.cn/simple  \
    && pip3 install  colorlog==5.0.1 -i https://pypi.tuna.tsinghua.edu.cn/simple  \
    && pip3 install  rich==12.2.0 -i https://pypi.tuna.tsinghua.edu.cn/simple  \
    && pip3 install  cryptography==36.0.1 -i https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /
COPY ./fluent-plugin-mysql-binlog-0.1.0.gem /fluent-plugin-mysql-binlog-0.1.0.gem
RUN fluent-gem install /fluent-plugin-mysql-binlog-0.1.0.gem
CMD ["fluentd"]