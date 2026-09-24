import axios, { AxiosInstance, AxiosError } from "axios";

const HOSPITABLE_BASE_URL = "https://public.api.hospitable.com/v2";

function createHospitableClient(): AxiosInstance {
  const apiKey = process.env.HOSPITABLE_API_KEY;
  if (!apiKey) {
    throw new Error("HOSPITABLE_API_KEY environment variable is required");
  }

  const client = axios.create({
    baseURL: HOSPITABLE_BASE_URL,
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    timeout: 30000,
  });

  client.interceptors.response.use(
    (response) => response,
    (error: AxiosError) => {
      if (error.response) {
        const status = error.response.status;
        const data = error.response.data as Record<string, unknown>;
        const message = (data?.message as string) || error.message;
        throw new Error(`Hospitable API Error ${status}: ${message}`);
      } else if (error.request) {
        throw new Error(`Hospitable API Network Error: No response received`);
      } else {
        throw new Error(`Hospitable API Error: ${error.message}`);
      }
    }
  );

  return client;
}

let _client: AxiosInstance | null = null;
export function getHospitable(): AxiosInstance {
  if (!_client) _client = createHospitableClient();
  return _client;
}

export function formatResponse(data: unknown): string {
  return JSON.stringify(data, null, 2);
}

export function handleError(error: unknown): string {
  if (error instanceof Error) return `Error: ${error.message}`;
  return `Error: ${String(error)}`;
}
