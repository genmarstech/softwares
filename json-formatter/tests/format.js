// console.log("Hello world")
// in the following i will be creating a sample of a high-quality JSON-formatter
// error, research and development are welcomed
const messyData = '{"user": {"id": 10293, "name": "Jane Doe"}, "status": "success"}';
// console.log(messyData)
const parsedData = JSON.parse(messyData);
const formattedData = JSON.stringify(parsedData, null, 2);
console.log(formattedData);
export {};
//# sourceMappingURL=format.js.map