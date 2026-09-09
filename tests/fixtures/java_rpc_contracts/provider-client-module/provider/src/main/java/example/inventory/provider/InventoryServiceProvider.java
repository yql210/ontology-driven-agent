package example.inventory.provider;

import example.inventory.api.InventoryService;
import org.apache.dubbo.config.annotation.DubboService;

@DubboService(interfaceClass = InventoryService.class, group = "inventory", version = "2.0")
public class InventoryServiceProvider implements InventoryService {
    @Override
    public String reserve(String sku) {
        return sku;
    }
}
