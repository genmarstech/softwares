
import { jsonrepair } from 'jsonrepair'


export const fixedData = (input: string): string => {
    try {
        const repaired = jsonrepair(input);
        const parsed = JSON.parse(repaired)
        return JSON.stringify(parsed, null, 2)
    } catch (error) {
        const message = error instanceof Error ? error.message:  "Unknown error"
        throw new Error (`invalid JSON ${message}`)
    }
}

