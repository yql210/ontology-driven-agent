package example.orders;

public interface OrderApi {
    OrderDto get(String id);
    OrderDto create();
    OrderDto lookup(String id);
    OrderDto lookup(long id);
    String getOrder(String id);
    void cancelOrder(String id);
}
