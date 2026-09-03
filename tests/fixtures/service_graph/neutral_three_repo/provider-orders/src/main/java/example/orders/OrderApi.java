package example.orders;

public interface OrderApi {
    String getOrder(String id);
    void cancelOrder(String id);
}
