package example.catalog;

import org.apache.dubbo.config.annotation.DubboService;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

interface CatalogApi {
    String lookup(String sku);
}

@DubboService(interfaceClass = CatalogApi.class, group = "catalog", version = "9.0")
class CatalogDubboService implements CatalogApi {
    private KafkaTemplate<String, Object> catalogKafka;
    private RabbitTemplate catalogRabbit;

    public String lookup(String sku) {
        catalogKafka.send("catalog-events", sku);
        catalogRabbit.convertAndSend("catalog.exchange", "catalog.lookup", sku);
        return sku;
    }
}
