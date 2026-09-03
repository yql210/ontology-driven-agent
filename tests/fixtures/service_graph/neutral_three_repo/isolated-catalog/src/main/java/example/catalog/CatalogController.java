package example.catalog;

import org.springframework.web.bind.annotation.GetMapping;

class OrderController {
    @GetMapping("/catalog-orders")
    Object unrelated() {
        return new Object();
    }
}
