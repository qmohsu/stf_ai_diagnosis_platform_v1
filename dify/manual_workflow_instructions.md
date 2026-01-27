# Manual Dify Workflow Setup

**Note**: The auto-import is currently experimental and may result in blank nodes in some Dify versions. If the graph is blank, please follow this 2-minute manual setup.

## 1. Create App
1.  Click **Create from Blank**.
2.  Name: `Auto Diagnosis Expert`
3.  Type: **Workflow**.
4.  Click **Create**.

## 2. Add Nodes
In the Workflow Editor, replicate this flow:

### Start Node
- Input Variables:
    1.  `vehicle_id` (Text, Required)
    2.  `symptoms` (Text, Required)

### Node A: HTTP Request (Redact)
- **URL**: `http://host.docker.internal:8000/v1/tools/redact`
- **Method**: `POST`
- **Body**: JSON
    ```json
    {
      "text": "{{#Start.symptoms#}}"
    }
    ```
- **Output Validation**: None (Default)

### Node A.1: Code (Parse Redaction)
- **Type**: Code
- **Language**: Python3
- **Input Variables**:
    - `response` (String) = `{{#Node_A.body#}}`
- **Code**:
    ```python
    import json
    def main(response: str) -> dict:
        try:
            data = json.loads(response)
            return {"redacted_text": data.get("redacted_text", "")}
        except:
            return {"redacted_text": ""}
    ```
- **Output Variables**:
    - `redacted_text` (String)

### Node B: HTTP Request (Validate VIN)
- **URL**: `http://host.docker.internal:8000/v1/tools/validate-vin`
- **Method**: `POST`
- **Body**: JSON
    ```json
    {
      "vin": "{{#Start.vehicle_id#}}"
    }
    ```

### Node B.1: Code (Parse Response)
*Since direct variable extraction can be tricky in some UI versions, use a Python node to parse the JSON.*

- **Type**: Code
- **Language**: Python3
- **Input Variables**:
    - `response` (String) = `{{#Node_B.body#}}`
- **Code**:
    ```python
    import json
    def main(response: str) -> dict:
        try:
            data = json.loads(response)
            # Return string "true" or "false" because Boolean type might be unavailable
            val = data.get("is_valid", False)
            return {"is_valid": "true" if val else "false"}
        except:
            return {"is_valid": "false"}
    ```
- **Output Variables**:
    - `is_valid` (String)

### Node C: Conditional (Check VIN)
- **IF**: `{{#Node_B_1.is_valid#}}` **IS** `true` (Text/String)
- **THEN**: Connect to Node D.
- **ELSE**: Connect to End (Failure).

### Node D: HTTP Request (Retrieve)
- **URL**: `http://host.docker.internal:8000/v1/rag/retrieve`
- **Method**: `POST`
- **Body**: JSON
    ```json
    {
      "query": "{{#Node_A_1.redacted_text#}}",
      "top_k": 3
    }
    ```

### Node D.1: Code (Parse Retrieval)
*Parse the chunks for the LLM.*
- **Type**: Code
- **Language**: Python3
- **Input Variables**:
    - `response` (String) = `{{#Node_D.body#}}`
- **Code**:
    ```python
    import json
    def main(response: str) -> dict:
        try:
            data = json.loads(response)
            # Convert list of chunks to a single string
            chunks = data.get("chunks", [])
            return {"context": "\n".join(chunks)}
        except:
            return {"context": ""}
    ```
- **Output Variables**:
    - `context` (String)

### Node E: LLM (Expert)
- **Model**: `llama3:8b` (via Ollama)
- **Prompt**:
    ```text
    You are an expert diagnostic technician.
    Analyze the following vehicle issue:
    
    Vehicle: {{#Start.vehicle_id#}}
    Symptoms: {{#Node_A_1.redacted_text#}}
    
    Retrieved Context:
    {{#Node_D_1.context#}}
    
    Provide a structured diagnosis.
    ```

## 3. Run
Click **run** and test with:
- **Vehicle ID**: `1FTEW1E...` (Any 17 char string)
- **Symptoms**: `Engine misfire at 60mph`
