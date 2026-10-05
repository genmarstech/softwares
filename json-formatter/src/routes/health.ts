import type { FastifyPluginAsync } from "fastify";

const health: FastifyPluginAsync = async (app) => {
    app.get("/health", () => {
        return {status: "OK"}
    })
};

export default health