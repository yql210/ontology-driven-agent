package example.orders.provider;

import example.orders.api.OrderService;
import example.orders.api.OrderSummary;
import org.apache.dubbo.config.annotation.DubboService;

@DubboService(interfaceClass = OrderService.class, group = "orders", version = "1.0", alias = "ambiguous")
public class AmbiguousOrderServiceProviderTwo implements OrderService {
    @Override
    public OrderSummary getOrder(String id) {
        return new OrderSummary(id);
    }
}
