package example.checkout;

import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.kafka.annotation.KafkaListener;

class ConfiguredMessagingConsumer {
    @KafkaListener(topics = "${configured.kafka.consumer-topic}", groupId = "${configured.kafka.consumer-group}")
    void consumeConfigured() {}

    @RabbitListener(queues = "${configured.rabbit.consumer-queue}", group = "${configured.rabbit.consumer-group}")
    void receiveConfigured() {}
}
