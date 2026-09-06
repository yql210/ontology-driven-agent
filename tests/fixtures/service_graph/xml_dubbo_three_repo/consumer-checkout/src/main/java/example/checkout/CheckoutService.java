package example.checkout;

import example.orders.OrderApi;

public class CheckoutService {
    private OrderApi orderApi;

    public String load() {
        return orderApi.find("42");
    }

    public String loadLong() {
        return orderApi.find(42L);
    }
}
