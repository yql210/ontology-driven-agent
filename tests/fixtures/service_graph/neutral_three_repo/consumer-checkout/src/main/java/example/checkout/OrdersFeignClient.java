package example.checkout;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;

@FeignClient(name = "orders", contextId = "orders-client", url = "http://orders.internal", path = "/")
@RequestMapping("/orders")
interface OrdersFeignClient {
    @GetMapping("/{id}") OrderDto get(String id);
    @PostMapping OrderDto create();
    @GetMapping("/lookup/by-key") OrderDto lookup(String id);
    @GetMapping("/lookup/by-number") OrderDto lookup(long id);
}

interface OrdinaryHttpInterface {
    @GetMapping("/orders/{id}") String ignored(String id);
}

@FeignClient(name = "${orders.name}", url = "${orders.url}", path = "${orders.path}")
interface DynamicOrdersFeignClient {
    @GetMapping("/{id}") OrderDto get(String id);
}

@FeignClient(name = "orders", path = "/orders")
interface MismatchedOrdersFeignClient {
    @GetMapping("/missing") OrderDto get(String id);
}

@FeignClient(name = "orders", path = "/orders")
interface AmbiguousOrdersFeignClient {
    @GetMapping("/ambiguous") @PostMapping("/ambiguous") OrderDto write(String id);
}

class FeignCheckout {
    private OrdersFeignClient orders;
    private DynamicOrdersFeignClient dynamicOrders;
    private MismatchedOrdersFeignClient mismatchedOrders;
    private AmbiguousOrdersFeignClient ambiguousOrders;
    private Object eager = orders.get("0");

    OrderDto load(String id) { return orders.get(id); }
    OrderDto create() { return orders.create(); }
    OrderDto lookup(String id) { return orders.lookup(id); }
    OrderDto lookup(long id) { return orders.lookup(id); }
    OrderDto dynamic(String id) { return dynamicOrders.get(id); }
    OrderDto mismatch(String id) { return mismatchedOrders.get(id); }
    OrderDto ambiguous(String id) { return ambiguousOrders.write(id); }
}

class OrderDto {}
