import fastify from "fastify";
// import health from "./routes/health.js";
import health from "./routes/health.js";
import format from "./routes/format.js";


const app = fastify({logger: true});
app.register(health);
app.register(format);


const start = async () => {
    try {
        await app.listen({port: 3000, host: "127.0.0.1"})
    } catch (error) {
        app.log.error(error)
        process.exit(1)
    }
}

start();