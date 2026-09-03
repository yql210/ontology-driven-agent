package example.orders;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping(path="/orders")
public class OrderController {
 @GetMapping("/{id}") public OrderDto get(String id) { return new OrderDto(); }
 @RequestMapping(path="/", method={RequestMethod.POST}) public OrderDto create() { return new OrderDto(); }
}
