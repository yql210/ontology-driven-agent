package example.checkout;

import example.inventory.api.InventoryService;
import org.apache.dubbo.config.annotation.DubboReference;

public class InventoryClient {
    @DubboReference(group = "inventory", version = "2.0")
    private InventoryService inventoryService;

    public String reserve(String sku) {
        return inventoryService.reserve(sku);
    }
}
