# JSON formatter

This is a simple backend application that uses fastify library to format broken JSON to well-structured JSON

## what it does.

converts invalid  JSON data to well-formatted data
takes any type wrong data and corrects it.

## v1 results
```
wrong data: 

{
  customer: {name: "Jane Wanjiku", phone: '+254700000000',},
  items: [
    {sku: 'A-01' qty: 2, price: 450,}
    {sku: 'B-07', qty: 1, price: 1200}
  ],
  paid: True,
  // pending delivery
  status: 'processing'


correct data: 

{
    "customer": {
        "name": "Jane Wanjiku",
        "phone": "+254700000000"
    },
    "items": [
        {
            "sku": "A-01",
            "qty": 2,
            "price": 450
        },
        {
            "sku": "B-07",
            "qty": 1,
            "price": 1200
        }
    ],
    "paid": true,
    "status": "processing"
}
 ```

 The tool basically corrects.

 ## Updates

 regular updates should be made and updated respectively hence the next update will be announced soon