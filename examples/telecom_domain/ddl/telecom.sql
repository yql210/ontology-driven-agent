-- Telecom domain DDL for ontology auto-generation
-- Domain: telecom billing & customer management
-- Contains: high-quality business tables, low-quality temp table, system table, M:N relation table

-- High quality: subscriber table (手机用户/订阅者)
CREATE TABLE subscriber (
    subscriber_id BIGINT PRIMARY KEY COMMENT '用户ID',
    msisdn VARCHAR(20) COMMENT '手机号码',
    imsi VARCHAR(20) COMMENT 'SIM卡标识',
    subscriber_name VARCHAR(100) COMMENT '用户姓名',
    id_number VARCHAR(20) COMMENT '身份证号',
    subscriber_type ENUM('prepaid', 'postpaid') COMMENT '用户类型: 预付费/后付费',
    register_date DATETIME COMMENT '入网日期',
    credit_level INT COMMENT '信用等级'
) COMMENT '手机用户表';

-- High quality: product table (电信套餐/产品)
CREATE TABLE telecom_product (
    product_id BIGINT PRIMARY KEY COMMENT '套餐ID',
    product_name VARCHAR(200) COMMENT '套餐名称',
    product_type ENUM('voice', 'data', 'combo', 'value_added') COMMENT '套餐类型: 语音/流量/融合/增值',
    monthly_fee DECIMAL(10,2) COMMENT '月租费',
    data_quota BIGINT COMMENT '流量配额(MB)',
    voice_quota INT COMMENT '语音配额(分钟)',
    description TEXT COMMENT '套餐描述'
) COMMENT '电信套餐表';

-- High quality: call detail record (通话详单)
CREATE TABLE call_record (
    record_id BIGINT PRIMARY KEY COMMENT '记录ID',
    calling_number VARCHAR(20) COMMENT '主叫号码',
    called_number VARCHAR(20) COMMENT '被叫号码',
    subscriber_id BIGINT COMMENT '用户ID',
    start_time DATETIME COMMENT '通话开始时间',
    duration INT COMMENT '通话时长(秒)',
    call_type ENUM('local', 'long_distance', 'roaming', 'international') COMMENT '通话类型: 本地/长途/漫游/国际',
    charge DECIMAL(10,2) COMMENT '通话费用',
    FOREIGN KEY (subscriber_id) REFERENCES subscriber(subscriber_id)
) COMMENT '通话详单表';

-- High quality: billing record (账单)
CREATE TABLE billing_record (
    bill_id BIGINT PRIMARY KEY COMMENT '账单ID',
    subscriber_id BIGINT COMMENT '用户ID',
    bill_period VARCHAR(10) COMMENT '账期(如202607)',
    total_amount DECIMAL(12,2) COMMENT '总金额',
    voice_charge DECIMAL(10,2) COMMENT '语音费用',
    data_charge DECIMAL(10,2) COMMENT '流量费用',
    vas_charge DECIMAL(10,2) COMMENT '增值业务费用',
    payment_status ENUM('unpaid', 'paid', 'overdue') COMMENT '缴费状态: 未缴/已缴/欠费',
    due_date DATETIME COMMENT '缴费截止日',
    FOREIGN KEY (subscriber_id) REFERENCES subscriber(subscriber_id)
) COMMENT '账单表';

-- High quality: data usage record (上网流量记录)
CREATE TABLE data_usage (
    usage_id BIGINT PRIMARY KEY COMMENT '流量记录ID',
    subscriber_id BIGINT COMMENT '用户ID',
    product_id BIGINT COMMENT '套餐ID',
    usage_time DATETIME COMMENT '使用时间',
    data_volume BIGINT COMMENT '流量(MB)',
    cell_id VARCHAR(50) COMMENT '基站ID',
    FOREIGN KEY (subscriber_id) REFERENCES subscriber(subscriber_id),
    FOREIGN KEY (product_id) REFERENCES telecom_product(product_id)
) COMMENT '上网流量记录表';

-- Low quality: temp table (no comments, meaningless columns) -- tests DDL completion
CREATE TABLE tmp_batch_2026 (
    f1 INT,
    f2 VARCHAR(50),
    f3 DATE,
    f4 DECIMAL(10,2)
);

-- System table: should NOT produce business relations -- tests FK filter
CREATE TABLE operation_log (
    log_id BIGINT PRIMARY KEY,
    op_user VARCHAR(50),
    op_action VARCHAR(100),
    op_time DATETIME,
    subscriber_id BIGINT,
    FOREIGN KEY (subscriber_id) REFERENCES subscriber(subscriber_id)
) COMMENT '系统操作日志表';

-- M:N relation table: subscriber subscribes to multiple products
CREATE TABLE subscriber_product (
    subscriber_id BIGINT,
    product_id BIGINT,
    subscribe_time DATETIME COMMENT '订购时间',
    PRIMARY KEY (subscriber_id, product_id),
    FOREIGN KEY (subscriber_id) REFERENCES subscriber(subscriber_id),
    FOREIGN KEY (product_id) REFERENCES telecom_product(product_id)
) COMMENT '用户-套餐订购关系表';
