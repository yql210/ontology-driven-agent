package example.orders;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.apache.dubbo.config.annotation.DubboService;

@DubboService(interfaceClass = OrderApi.class, group = "orders", version = "1.0")
public class OrderDubboService implements OrderApi {
    private KafkaTemplate<String, Object> kafkaTemplate;
    private RabbitTemplate rabbitTemplate;

    @Override
    public String getOrder(String id) {
        kafkaTemplate.send("order-events", id);
        rabbitTemplate.convertAndSend("order.exchange", "order.created", id);
        return id;
    }

    @Override
    public void cancelOrder(String id) {
        kafkaTemplate.send(dynamicTopic, id);
    }
}
