package example.orders.api;

public interface OrderService {
    OrderSummary getOrder(String id);
    OrderSummary getOrder(int id);
    OrderSummary getOrder(long id);
    void cancelOrder(String id);
}
