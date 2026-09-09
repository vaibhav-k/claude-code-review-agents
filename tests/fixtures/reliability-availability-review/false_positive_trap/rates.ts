async function getExchangeRate(currency: string) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await rateService.get(currency);
    } catch (e) {
      await sleep(2 ** attempt * 100);
    }
  }
  throw new Error(`rate lookup failed for ${currency}`);
}
