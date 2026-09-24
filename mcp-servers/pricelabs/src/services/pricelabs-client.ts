import axios, { AxiosInstance, AxiosError } from "axios";

const PRICELABS_BASE_URL = "https://api.pricelabs.co";

function createPriceLabsClient(): AxiosInstance {
  const apiKey = process.env.PRICELABS_API_KEY;
  if (!apiKey) {
    throw new Error("PRICELABS_API_KEY environment variable is required");
  }

  const client = axios.create({
    baseURL: PRICELABS_BASE_URL,
    headers: {
      "X-API-Key": apiKey,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    timeout: 300000, // PriceLabs recommends 300s timeout
  });

  client.interceptors.response.use(
    (response) => response,
    (error: AxiosError) => {
      if (error.response) {
        const status = error.response.status;
        const data = error.response.data as Record<string, unknown>;
        const message = (data?.message as string) || (data?.error as string) || error.message;
        if (status === 429) {
          throw new Error(`PriceLabs Rate Limited (429): Max 60 req/min, 1000 req/hr. ${message}`);
        }
        throw new Error(`PriceLabs API Error ${status}: ${message}`);
      } else if (error.request) {
        throw new Error(`PriceLabs API Network Error: No response received`);
      } else {
        throw new Error(`PriceLabs API Error: ${error.message}`);
      }
    }
  );

  return client;
}

let _client: AxiosInstance | null = null;
export function getPriceLabs(): AxiosInstance {
  if (!_client) _client = createPriceLabsClient();
  return _client;
}

export function formatResponse(data: unknown): string {
  return JSON.stringify(data, null, 2);
}

export function handleError(error: unknown): string {
  if (error instanceof Error) return `Error: ${error.message}`;
  return `Error: ${String(error)}`;
}
