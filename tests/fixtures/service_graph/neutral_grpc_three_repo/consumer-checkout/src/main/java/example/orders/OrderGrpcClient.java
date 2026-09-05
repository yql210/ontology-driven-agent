package example.orders;

class GetOrderRequest {}
class GetOrderResponse {}
class StreamObserver<T> {}
class Channel {}

class OrderServiceGrpc {
    static class OrderServiceBlockingStub {
        GetOrderResponse getOrder(GetOrderRequest request) { return null; }
    }
    static class OrderServiceStub {
        void getOrder(GetOrderRequest request, StreamObserver<GetOrderResponse> observer) {}
    }
    static OrderServiceBlockingStub newBlockingStub(Channel channel) { return null; }
    static OrderServiceStub newStub(Channel channel) { return null; }
}

class OrderGrpcClient {
    private OrderServiceGrpc.OrderServiceBlockingStub blockingStub;

    GetOrderResponse blocking(Channel channel, GetOrderRequest request) {
        blockingStub = OrderServiceGrpc.newBlockingStub(channel);
        return blockingStub.getOrder(request);
    }

    void async(Channel channel, GetOrderRequest request, StreamObserver<GetOrderResponse> observer) {
        OrderServiceGrpc.OrderServiceStub stub = OrderServiceGrpc.newStub(channel);
        stub.getOrder(request, observer);
    }

    String helper(String value) { return value.toUpperCase(); }
}
