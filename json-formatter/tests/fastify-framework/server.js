import fastify from "fastify";
const app = fastify({ logger: true });
app.get("/health", async () => {
    return { status: "ok" };
});
const start = async () => {
    try {
        await app.listen({ port: 3000, host: "127.0.0.1" });
    }
    catch (error) {
        app.log.error(error);
        process.exit(1);
    }
};
start();
//# sourceMappingURL=server.js.map