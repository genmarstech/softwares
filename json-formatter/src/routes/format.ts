import type { FastifyPluginAsync } from "fastify";
import { fixedData } from "../services/json.js";
import { createHash } from "node:crypto";

const format: FastifyPluginAsync = async (app) => {
  app.post<{ Body: string }>(
    "/format",
    {
      schema: {
        body: { type: "string" },
      },
    },
    async (req, reply) => {
      try {
        const cachedKey = `json:${createHash("sha256").update(req.body).digest("hex")}`
        const formatted = await fixedData(app, req.body, cachedKey)
        
        return reply.type("application/json").send(formatted)
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown error";
        return reply.code(400).send({ message });
      }
    }
  );
};

export default format;