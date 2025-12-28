import json
import urllib.request
import sys

# Configuration
API_URL = "http://localhost:8097/v1/chat/completions"
MODEL_ID = "qwen2.5-0.5b-instruct-q4_k_m.gguf"

def chat_stream(prompt):
    headers = {"Content-Type": "application/json"}
    data = {
        "model": MODEL_ID,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}]
    }

    req = urllib.request.Request(
        API_URL, 
        data=json.dumps(data).encode('utf-8'), 
        headers=headers
    )

    print(f"User: {prompt}\nAssistant: ", end="", flush=True)

    try:
        # Establish connection
        with urllib.request.urlopen(req) as response:
            # Iterate over the raw stream line by line
            for line in response:
                line = line.decode('utf-8').strip()
                
                # Filter for SSE data lines
                if line.startswith("data: "):
                    content = line[6:] # Strip "data: " prefix
                    
                    if content == "[DONE]":
                        break
                    
                    try:
                        chunk = json.loads(content)
                        # Extract the token
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            # Write to stdout immediately (no buffering)
                            sys.stdout.write(delta)
                            sys.stdout.flush()
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        print(f"\n[Connection Error]: {e}")
    
    print("\n")

if __name__ == "__main__":
    chat_stream("who is james garfield?")