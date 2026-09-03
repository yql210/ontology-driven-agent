package example.checkout;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;
class CheckoutService {
 void run(String id) {
  new RestTemplate().getForObject("http://orders.internal/orders/1", Object.class);
  new RestTemplate().getForObject(baseUrl + id, Object.class);
  WebClient client = WebClient.create();
  client.get().uri("http://orders.internal/orders/2");
  client.post().uri("http://orders.internal/orders");
  client.put().uri("http://orders.internal/orders/2");
  client.delete().uri("http://orders.internal/orders/2");
  client.get().uri(baseUrl + id);
 }
}
