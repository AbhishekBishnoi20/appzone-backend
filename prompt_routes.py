from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import sqlite3
from collections import defaultdict
from datetime import datetime
import json
from jinja2 import Template

router = APIRouter()
security = HTTPBasic()

def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = "admin"
    correct_password = "admin"
    if credentials.username != correct_username or credentials.password != correct_password:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

# HTML template moved to a separate constant at the top
PROMPTS_TEMPLATE = '''
<html>
        <head>
            <title>User Prompts</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                }
                h1 {
                    color: #333;
                    border-bottom: 2px solid #eee;
                    padding-bottom: 10px;
                }
                .date, .back-button {
                    cursor: pointer;
                    margin: 20px 0;
                    padding: 10px;
                    background-color: #e0e0e0;
                    border-radius: 5px;
                    text-align: center;
                }
                .prompts {
                    list-style-type: none;
                    padding: 0;
                }
                .prompts li {
                    padding: 15px;
                    margin: 10px 0;
                    background-color: #f9f9f9;
                    border-radius: 5px;
                    border: 1px solid #eee;
                }
                .prompts li:hover {
                    background-color: #f0f0f0;
                }
                .hidden {
                    display: none;
                }
                .image-link {
                    display: inline-block;
                    margin: 5px;
                    padding: 5px 10px;
                    background-color: #007bff;
                    color: white;
                    text-decoration: none;
                    border-radius: 3px;
                    font-size: 0.9em;
                }
                .image-link:hover {
                    background-color: #0056b3;
                }
                .prompt-container {
                    margin-bottom: 15px;
                }
                .image-container {
                    margin-top: 10px;
                }
                .delete-btn {
                    background-color: #dc3545;
                    color: white;
                    border: none;
                    padding: 5px 10px;
                    border-radius: 3px;
                    cursor: pointer;
                    margin-left: 10px;
                }
                .delete-btn:hover {
                    background-color: #c82333;
                }
                .date-actions {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }
                .prompt-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: flex-start;
                }
            </style>
            <script>
                function showPrompts(date) {
                    document.getElementById('dates').classList.add('hidden');
                    document.getElementById('prompts-' + date).classList.remove('hidden');
                    document.getElementById('back-button').classList.remove('hidden');
                    // Set the URL hash when showing prompts
                    window.location.hash = date;
                }

                function showDates() {
                    document.getElementById('dates').classList.remove('hidden');
                    document.querySelectorAll('.prompts').forEach(el => el.classList.add('hidden'));
                    document.getElementById('back-button').classList.add('hidden');
                    // Clear the URL hash when showing dates
                    window.location.hash = '';
                }

                // Check URL hash on page load
                window.onload = function() {
                    const date = window.location.hash.slice(1); // Remove the # symbol
                    if (date && document.getElementById('prompts-' + date)) {
                        showPrompts(date);
                    }
                };

                async function deletePrompt(id, date) {
                    if (confirm('Are you sure you want to delete this prompt?')) {
                        try {
                            const response = await fetch(`/prompts/${id}`, {
                                method: 'DELETE'
                            });
                            if (response.ok) {
                                document.getElementById(`prompt-${id}`).remove();
                                // If no more prompts for this date, hide the date
                                const datePrompts = document.querySelectorAll(`#prompts-${date} li`);
                                if (datePrompts.length === 0) {
                                    document.getElementById(`date-${date}`).remove();
                                    document.getElementById(`prompts-${date}`).remove();
                                }
                            } else {
                                alert('Failed to delete prompt');
                            }
                        } catch (error) {
                            alert('Error deleting prompt');
                        }
                    }
                }

                async function deleteDate(date) {
                    if (confirm('Are you sure you want to delete all prompts for this date?')) {
                        try {
                            const response = await fetch(`/prompts/date/${date}`, {
                                method: 'DELETE'
                            });
                            if (response.ok) {
                                document.getElementById(`date-${date}`).remove();
                                document.getElementById(`prompts-${date}`).remove();
                                showDates();
                            } else {
                                alert('Failed to delete date');
                            }
                        } catch (error) {
                            alert('Error deleting date');
                        }
                    }
                }
            </script>
        </head>
        <body>
            <h1>User Prompts</h1>
            <div id="back-button" class="back-button hidden" onclick="showDates()">Back to Dates</div>
            <div id="dates">
                {% for date in grouped_prompts.keys() %}
                    <div id="date-{{ date }}" class="date-actions">
                        <div class="date" onclick="showPrompts('{{ date }}')">
                            {{ date }}
                        </div>
                        <button class="delete-btn" onclick="deleteDate('{{ date }}')">Delete All</button>
                    </div>
                {% endfor %}
            </div>
            {% for date, prompts in grouped_prompts.items() %}
                <ul class="prompts hidden" id="prompts-{{ date }}">
                    {% for id, prompt, images in prompts %}
                        <li id="prompt-{{ id }}" class="prompt-container">
                            <div class="prompt-header">
                                <div class="prompt-text">{{ prompt }}</div>
                                <button class="delete-btn" onclick="deletePrompt({{ id }}, '{{ date }}')">Delete</button>
                            </div>
                            {% if images %}
                                <div class="image-container">
                                    {% for image_url in images %}
                                        <a href="{{ image_url }}" target="_blank" class="image-link">
                                            View Image {{ loop.index }}
                                        </a>
                                    {% endfor %}
                                </div>
                            {% endif %}
                        </li>
                    {% endfor %}
                </ul>
            {% endfor %}
        </body>
    </html>
    '''

@router.get("/prompts", response_class=HTMLResponse)
async def get_prompts(auth: bool = Depends(authenticate)):
    conn = sqlite3.connect('prompts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, prompt, image_urls, timestamp FROM prompts ORDER BY timestamp DESC')
    prompts = cursor.fetchall()
    conn.close()

    grouped_prompts = defaultdict(list)
    for id, prompt, image_urls, timestamp in prompts:
        date = datetime.fromisoformat(timestamp).date()
        image_list = json.loads(image_urls) if image_urls else []
        grouped_prompts[date].append((id, prompt, image_list))

    template = Template(PROMPTS_TEMPLATE)
    return template.render(grouped_prompts=grouped_prompts)

@router.delete("/prompts/date/{date}")
async def delete_date_prompts(date: str):
    try:
        conn = sqlite3.connect('prompts.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM prompts WHERE DATE(timestamp) = ?', (date,))
        conn.commit()
        conn.close()
        return {"message": f"All prompts for {date} deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: int):
    try:
        conn = sqlite3.connect('prompts.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM prompts WHERE id = ?', (prompt_id,))
        conn.commit()
        conn.close()
        return {"message": f"Prompt {prompt_id} deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 