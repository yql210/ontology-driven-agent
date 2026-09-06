package example.orders;

import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.kafka.core.KafkaTemplate;

class ConfiguredMessagingProducer {
    private KafkaTemplate<String, Object> kafkaTemplate;
    private RabbitTemplate rabbitTemplate;

    void publish(String id) {
        kafkaTemplate.send("${configured.kafka.producer-topic}", id);
        rabbitTemplate.convertAndSend("${configured.rabbit.producer-queue}", "configured.created", id);
    }
}
