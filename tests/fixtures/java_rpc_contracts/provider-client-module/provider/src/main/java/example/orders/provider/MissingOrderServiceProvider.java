package example.orders.provider;

import example.orders.api.OrderService;
import org.apache.dubbo.config.annotation.DubboService;

@DubboService(interfaceClass = OrderService.class, group = "missing", version = "1.0")
public abstract class MissingOrderServiceProvider implements OrderService {}
