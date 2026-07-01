"""Phase 2.4 — Capability Discovery Evaluation Set.

≥50 sub-goals with gold-standard capability matches, used to measure
top-3 recall rate for CapabilityFinder semantic search.

Design: 10 capability domains × 5-6 sub-goals each = 53 total.
Gold answers are capability names that SHOULD appear in top-3 results.
"""

from __future__ import annotations

from typing import NamedTuple


class EvalCase(NamedTuple):
    """A single evaluation case.

    Attributes:
        sub_goal: Natural language sub-goal text.
        gold_ids: Expected capability IDs (at least one should be in top-3).
        domain: Business domain for domain-filtered tests.
    """

    sub_goal: str
    gold_ids: list[str]
    domain: str | None = None


# 10 business domains with realistic capabilities
CAPABILITY_SPECS: dict[str, list[tuple[str, str, str, str]]] = {
    # (id, name, description, domain)
    "payment": [
        ("cap-pay-1", "process_payment", "处理用户支付请求，支持信用卡、借记卡和第三方支付", "payment"),
        ("cap-pay-2", "validate_payment_method", "验证支付方式有效性，检查卡号、有效期和CVV", "payment"),
        ("cap-pay-3", "refund_payment", "处理退款请求，原路退回用户付款", "payment"),
        ("cap-pay-4", "calculate_tax", "根据地区和商品类型计算应缴税费", "payment"),
        ("cap-pay-5", "generate_invoice", "生成电子发票并发送给用户", "payment"),
    ],
    "inventory": [
        ("cap-inv-1", "check_stock", "查询商品实时库存状态", "inventory"),
        ("cap-inv-2", "reserve_inventory", "预留库存以防超卖", "inventory"),
        ("cap-inv-3", "update_stock", "更新商品库存数量（入库/出库）", "inventory"),
        ("cap-inv-4", "low_stock_alert", "库存低于阈值时发送告警通知", "inventory"),
        ("cap-inv-5", "batch_stock_sync", "批量同步多仓库库存数据", "inventory"),
    ],
    "order": [
        ("cap-ord-1", "create_order", "创建新订单，包含商品、地址和优惠信息", "order"),
        ("cap-ord-2", "cancel_order", "取消未发货订单并触发退款", "order"),
        ("cap-ord-3", "query_order_status", "查询订单当前状态和物流信息", "order"),
        ("cap-ord-4", "split_order", "将订单按仓库拆分为多个子订单", "order"),
        ("cap-ord-5", "merge_orders", "合并同一用户的多个待付款订单", "order"),
        ("cap-ord-6", "apply_coupon", "应用优惠券计算折后价格", "order"),
    ],
    "logistics": [
        ("cap-log-1", "ship_order", "生成运单并发起发货流程", "logistics"),
        ("cap-log-2", "track_shipment", "查询物流轨迹和预计送达时间", "logistics"),
        ("cap-log-3", "calculate_shipping_fee", "根据重量、距离和时效计算运费", "logistics"),
        ("cap-log-4", "reroute_shipment", "修改配送地址重新路由包裹", "logistics"),
        ("cap-log-5", "confirm_delivery", "确认用户签收并完结物流单", "logistics"),
    ],
    "user": [
        ("cap-usr-1", "register_user", "新用户注册，创建账户并发送验证邮件", "user"),
        ("cap-usr-2", "authenticate_user", "用户登录认证，支持密码和短信验证码", "user"),
        ("cap-usr-3", "update_profile", "更新用户个人信息和偏好设置", "user"),
        ("cap-usr-4", "reset_password", "通过邮箱或手机重置密码", "user"),
        ("cap-usr-5", "delete_account", "注销用户账户并清理关联数据", "user"),
    ],
    "product": [
        ("cap-prd-1", "search_products", "全文搜索商品，支持分类筛选和排序", "product"),
        ("cap-prd-2", "get_product_detail", "获取商品详情，包含规格、评价和价格", "product"),
        ("cap-prd-3", "recommend_products", "基于用户行为和协同过滤推荐商品", "product"),
        ("cap-prd-4", "compare_products", "对比多个商品的规格和价格", "product"),
        ("cap-prd-5", "review_product", "提交和审核商品评价", "product"),
        ("cap-prd-6", "sync_product_catalog", "从ERP同步商品目录到电商平台", "product"),
    ],
    "notification": [
        ("cap-ntf-1", "send_email", "发送事务性邮件（验证、通知、营销）", "notification"),
        ("cap-ntf-2", "send_sms", "发送短信验证码和营销短信", "notification"),
        ("cap-ntf-3", "send_push", "推送App通知和Web通知", "notification"),
        ("cap-ntf-4", "schedule_notification", "定时发送通知（活动预告、订单提醒）", "notification"),
        ("cap-ntf-5", "manage_subscription", "管理用户通知订阅偏好", "notification"),
    ],
    "analytics": [
        ("cap-anl-1", "generate_sales_report", "生成销售报表，按时间/渠道/品类聚合", "analytics"),
        ("cap-anl-2", "user_behavior_analysis", "用户行为分析：漏斗、留存、转化率", "analytics"),
        ("cap-anl-3", "real_time_dashboard", "实时业务大盘：GMV、订单量、活跃用户", "analytics"),
        ("cap-anl-4", "ab_test_analysis", "A/B测试结果统计显著性分析", "analytics"),
        ("cap-anl-5", "anomaly_detection", "检测异常流量和交易欺诈", "analytics"),
    ],
    "content": [
        ("cap-cnt-1", "publish_article", "发布和编辑CMS内容", "content"),
        ("cap-cnt-2", "upload_media", "上传图片和视频到CDN", "content"),
        ("cap-cnt-3", "moderate_content", "内容审核：敏感词、违规图片检测", "content"),
        ("cap-cnt-4", "translate_content", "多语言内容翻译和本地化", "content"),
        ("cap-cnt-5", "schedule_publish", "定时发布和内容版本管理", "content"),
    ],
    "security": [
        ("cap-sec-1", "rate_limit", "API请求频率限制和流控", "security"),
        ("cap-sec-2", "fraud_detection", "交易欺诈检测和行为风控", "security"),
        ("cap-sec-3", "audit_log", "记录和查询操作审计日志", "security"),
        ("cap-sec-4", "encrypt_data", "敏感数据加密存储和脱敏", "security"),
        ("cap-sec-5", "grant_permission", "RBAC权限授予和撤销", "security"),
    ],
}

# 53 evaluation cases — more than the required 50
EVAL_CASES: list[EvalCase] = [
    # === Payment ===
    EvalCase("处理用户的信用卡付款", ["cap-pay-1"], "payment"),
    EvalCase("验证支付方式是否有效", ["cap-pay-2"], "payment"),
    EvalCase("退款给用户", ["cap-pay-3"], "payment"),
    EvalCase("计算订单税费", ["cap-pay-4"], "payment"),
    EvalCase("开具发票", ["cap-pay-5"], "payment"),
    # === Inventory ===
    EvalCase("查看商品还有多少库存", ["cap-inv-1"], "inventory"),
    EvalCase("预留库存防止超卖", ["cap-inv-2"], "inventory"),
    EvalCase("入库更新库存数量", ["cap-inv-3"], "inventory"),
    EvalCase("库存不足时发送提醒", ["cap-inv-4"], "inventory"),
    EvalCase("同步多个仓库的库存", ["cap-inv-5"], "inventory"),
    # === Order ===
    EvalCase("创建一个新订单", ["cap-ord-1"], "order"),
    EvalCase("取消这个订单", ["cap-ord-2"], "order"),
    EvalCase("查询订单物流到哪了", ["cap-ord-3"], "order"),
    EvalCase("按仓库拆分订单", ["cap-ord-4"], "order"),
    EvalCase("合并两个未支付的订单", ["cap-ord-5"], "order"),
    EvalCase("使用优惠券", ["cap-ord-6"], "order"),
    # === Logistics ===
    EvalCase("发货生成运单号", ["cap-log-1"], "logistics"),
    EvalCase("查快递到哪了", ["cap-log-2"], "logistics"),
    EvalCase("计算运费多少钱", ["cap-log-3"], "logistics"),
    EvalCase("修改收货地址重新配送", ["cap-log-4"], "logistics"),
    EvalCase("确认用户已签收", ["cap-log-5"], "logistics"),
    # === User ===
    EvalCase("注册新用户账号", ["cap-usr-1"], "user"),
    EvalCase("用户登录认证", ["cap-usr-2"], "user"),
    EvalCase("修改个人信息", ["cap-usr-3"], "user"),
    EvalCase("忘记密码了要重置", ["cap-usr-4"], "user"),
    EvalCase("注销这个账号", ["cap-usr-5"], "user"),
    # === Product ===
    EvalCase("搜索商品", ["cap-prd-1"], "product"),
    EvalCase("查看商品详情页面", ["cap-prd-2"], "product"),
    EvalCase("推荐用户可能喜欢的商品", ["cap-prd-3"], "product"),
    EvalCase("对比两个商品", ["cap-prd-4"], "product"),
    EvalCase("给商品写评价", ["cap-prd-5"], "product"),
    EvalCase("同步商品目录", ["cap-prd-6"], "product"),
    # === Notification ===
    EvalCase("给用户发一封邮件", ["cap-ntf-1"], "notification"),
    EvalCase("发送短信验证码", ["cap-ntf-2"], "notification"),
    EvalCase("推送一条App消息", ["cap-ntf-3"], "notification"),
    EvalCase("定时发送促销通知", ["cap-ntf-4"], "notification"),
    EvalCase("退订营销邮件", ["cap-ntf-5"], "notification"),
    # === Analytics ===
    EvalCase("生成上个月销售报表", ["cap-anl-1"], "analytics"),
    EvalCase("分析用户转化漏斗", ["cap-anl-2"], "analytics"),
    EvalCase("看实时交易大盘", ["cap-anl-3"], "analytics"),
    EvalCase("A/B测试哪个版本好", ["cap-anl-4"], "analytics"),
    EvalCase("检测异常交易", ["cap-anl-5"], "analytics"),
    # === Content ===
    EvalCase("发布一篇新文章", ["cap-cnt-1"], "content"),
    EvalCase("上传商品图片", ["cap-cnt-2"], "content"),
    EvalCase("审核用户评论是否违规", ["cap-cnt-3"], "content"),
    EvalCase("翻译成英文版本", ["cap-cnt-4"], "content"),
    EvalCase("定时发布明天上午的文章", ["cap-cnt-5"], "content"),
    # === Security ===
    EvalCase("限制API调用频率", ["cap-sec-1"], "security"),
    EvalCase("检测欺诈交易", ["cap-sec-2"], "security"),
    EvalCase("查看操作日志", ["cap-sec-3"], "security"),
    EvalCase("加密用户的手机号", ["cap-sec-4"], "security"),
    EvalCase("给运营角色授权", ["cap-sec-5"], "security"),
]

# Verify we have ≥50 cases
assert len(EVAL_CASES) >= 50, f"Expected ≥50 eval cases, got {len(EVAL_CASES)}"
