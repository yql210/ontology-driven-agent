package example.checkout;

import example.orders.api.OrderService;
import example.orders.api.OrderSummary;
import org.apache.dubbo.config.annotation.DubboReference;

public class CheckoutService {
    @DubboReference(group = "orders", version = "1.0")
    private OrderService orderService;
    @DubboReference(group = "orders", version = "2.0")
    private OrderService versionConflictService;
    @DubboReference(group = "orders", version = "1.0", alias = "ambiguous")
    private OrderService ambiguousOrderService;
    @DubboReference(group = "missing", version = "1.0")
    private OrderService missingOrderService;
    @DubboReference(group = "${orders.group}", version = "1.0")
    private OrderService dynamicOrderService;

    public OrderSummary checkoutById(String id) {
        return orderService.getOrder(id);
    }

    public OrderSummary checkoutLiteral() {
        return orderService.getOrder("A-100");
    }

    public OrderSummary checkoutInt() {
        return orderService.getOrder(100);
    }

    public OrderSummary checkoutLong() {
        return orderService.getOrder(100L);
    }

    public OrderSummary checkoutVersionConflict() {
        return versionConflictService.getOrder("A-200");
    }

    public OrderSummary checkoutAmbiguous() {
        return ambiguousOrderService.getOrder("A-201");
    }

    public OrderSummary checkoutMissingProvider() {
        return missingOrderService.getOrder("A-202");
    }

    public OrderSummary checkoutDynamic() {
        return dynamicOrderService.getOrder("A-203");
    }

    public void checkoutTwice() {
        orderService.cancelOrder("A-101");
        orderService.cancelOrder("A-102");
    }
}
