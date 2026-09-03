package example.checkout;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;

import org.apache.dubbo.config.annotation.DubboReference;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import example.orders.OrderApi;

class CheckoutService {
    @DubboReference(interfaceClass = example.orders.OrderApi.class, group = "orders", version = "1.0")
    private OrderApi orderApi;
    private KafkaTemplate<String, Object> kafkaTemplate;
    private RabbitTemplate rabbitTemplate;

    @KafkaListener(topics = {"order-events", "payments"}, groupId = "checkout")
    void consume() {
        orderApi.getOrder("42");
        kafkaTemplate.send(dynamicTopic, "payload");
    }

    @RabbitListener(queues = {"order.queue", "audit.queue"}, group = "checkout-workers")
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
