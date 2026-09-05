package example.catalog;

class GetOrderRequest {}
class GetOrderResponse {}
class StreamObserver<T> {}

class CatalogServiceGrpc {
    abstract static class CatalogServiceImplBase {}
}

class CatalogGrpcService extends CatalogServiceGrpc.CatalogServiceImplBase {
    @Override
    public void getOrder(GetOrderRequest request, StreamObserver<GetOrderResponse> observer) {}
}
