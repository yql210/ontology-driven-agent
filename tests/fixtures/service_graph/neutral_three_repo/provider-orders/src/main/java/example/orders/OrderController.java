package example.orders;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping(path="/orders")
public class OrderController implements OrderApi {
 @GetMapping("/{id}") public OrderDto get(String id) { return new OrderDto(); }
 @RequestMapping(path="/", method={RequestMethod.POST}) public OrderDto create() { return new OrderDto(); }
 @GetMapping("/lookup/by-key") public OrderDto lookup(String id) { return new OrderDto(); }
 @GetMapping("/lookup/by-number") public OrderDto lookup(long id) { return new OrderDto(); }
}
