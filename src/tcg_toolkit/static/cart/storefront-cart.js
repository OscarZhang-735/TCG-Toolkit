export function storefrontCartClient(client, adaptRows) {
  const withRows = result => ({...result, cart: adaptRows(result.cart)});
  return {
    hasCredentials: () => client.hasCredentials(),
    query: async () => adaptRows(await client.query()),
    add: async ({product_url, counter}, key) => withRows(await client.add({product_url, counter}, key)),
    setQuantity: async (row, quantity) => withRows(await client.setQuantity(row.type, quantity)),
    remove: async row => withRows(await client.remove(row.type)),
  };
}
