import fastify from "fastify";
// import health from "./routes/health.js";
import health from "./routes/health.js";
import format from "./routes/format.js";
import fastifyRedis from "@fastify/redis";



const app = fastify({logger: true});

const redisurl = process.env.REDIS_URL;

if (!redisurl) {
    throw new Error ('Redis url not set')
}

app.register(health);
app.register(format);
app.register(fastifyRedis, {
    url: redisurl,
    connectTimeout: 5000
})


const start = async () => {
    try {
        await app.listen({port: 3000, host: "127.0.0.1"})
    } catch (error) {
        app.log.error(error)
        process.exit(1)
    }
}

start();