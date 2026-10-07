import type { FastifyInstance } from 'fastify';
import { jsonrepair } from 'jsonrepair';

export const fixedData = async (
  fastify: FastifyInstance,
  input: unknown,
  cacheKey: string,
  ttlInSeconds = 300,
): Promise<string> => {
  if (typeof input !== 'string') {
    throw new TypeError(`Input must be a JSON string got ${typeof input}`);
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonrepair(input));
  } catch (error) {
    throw new Error(
      `Invalid JSON: ${error instanceof Error ? error.message : 'Unknown error'}`,
      { cause: error },
    );
  }

  // Cache failures shouldn't kill the request or be reported as bad JSON
  try {
    await fastify.redis.setex(cacheKey, ttlInSeconds, JSON.stringify(parsed));
    console.log(`data has been saved successfully, and will be deleted in ${ttlInSeconds}`)
  } catch (error) {
    fastify.log.warn({ err: error, cacheKey }, 'Failed to cache repaired JSON');
  }

  return JSON.stringify(parsed, null, 2);
};