package example.catalog;

import org.springframework.kafka.core.KafkaTemplate;

class ConfiguredCatalogPublisher {
    private KafkaTemplate<String, Object> kafkaTemplate;

    void publishCatalogUpdate(String sku) {
        kafkaTemplate.send("configured-catalog-events", sku);
    }
}
