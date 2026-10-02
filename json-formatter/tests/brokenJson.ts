// this file we will test how broken json can be handled and parsed

const brokenJson = '{"user":{"id":10293,"name":"Jane Doe"}, "status": "success"}';

try {
    const parsedData = JSON.parse(brokenJson)

    const formattedData = JSON.stringify(parsedData, null, 2)

    console.log('SUCCESS! Data parsed', formattedData)

} catch (error) {

    console.error('Error data parse failed!')
    console.error('error message !', error.message)
    
}