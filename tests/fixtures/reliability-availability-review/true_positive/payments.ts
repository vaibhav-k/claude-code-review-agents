async function chargeCard(orderId: string, amount: number) {
  while (true) {
    try {
      return await paymentGateway.charge(orderId, amount);
    } catch (e) {
      continue; // retry immediately
    }
  }
}
