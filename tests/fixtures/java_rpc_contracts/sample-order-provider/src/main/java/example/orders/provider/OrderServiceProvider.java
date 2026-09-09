package example.orders.provider;

import example.orders.api.OrderService;
import example.orders.api.OrderSummary;
import org.apache.dubbo.config.annotation.DubboService;

@DubboService(interfaceClass = OrderService.class, group = "orders", version = "1.0")
public class OrderServiceProvider implements OrderService {
    @Override
    public OrderSummary getOrder(String id) {
        return new OrderSummary(id);
    }

    @Override
    public OrderSummary getOrder(long id) {
        return new OrderSummary(Long.toString(id));
    }

    @Override
    public void cancelOrder(String id) {}
}
