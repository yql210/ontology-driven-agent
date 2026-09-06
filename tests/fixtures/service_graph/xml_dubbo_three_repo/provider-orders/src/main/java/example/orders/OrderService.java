package example.orders;

public class OrderService implements OrderApi {
    public String find(String id) {
        return id;
    }

    public String find(long id) {
        return Long.toString(id);
    }
}
