package example.orders;

class GetOrderRequest {}
class GetOrderResponse {}
class StreamObserver<T> {}

class OrderServiceGrpc {
    abstract static class OrderServiceImplBase {}
}

class OrderGrpcService extends OrderServiceGrpc.OrderServiceImplBase {
    @Override
    public void getOrder(GetOrderRequest request, StreamObserver<GetOrderResponse> observer) {}
}
