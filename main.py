#!/usr/bin/env python3
"""
R4J M1SHR4 - Facebook Message Automation Bot
Pure Python backend script - MEMORY LEAK FIXED VERSION
Supports both Regular and E2EE chats with automatic browser restart
"""

import os
import sys
import time
import json
import logging
import threading
import traceback
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import sqlite3

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    WebDriverException,
    NoSuchElementException,
    TimeoutException
)


# ============================================================================
# CONFIGURATION
# ============================================================================

BASE_DIR = Path(__file__).parent

# File paths
COOKIES_FILE = BASE_DIR / 'cookies.txt'
HATERS_FILE = BASE_DIR / 'hatersname.txt'
THREAD_ID_FILE = BASE_DIR / 'thread_id.txt'
TIME_FILE = BASE_DIR / 'time.txt'
LASTNAME_FILE = BASE_DIR / 'lastname.txt'
MESSAGES_FILE = BASE_DIR / 'File.txt'

# Database
DB_PATH = BASE_DIR / 'automation.db'

# Logging setup
LOG_FILE = BASE_DIR / 'automation.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# DATABASE MANAGER
# ============================================================================

class DatabaseManager:
    """Manages SQLite database for state persistence"""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def init_db(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # State table for automation tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS automation_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                current_cookie_index INTEGER DEFAULT 0,
                current_message_index INTEGER DEFAULT 0,
                current_haters_index INTEGER DEFAULT 0,
                total_messages_sent INTEGER DEFAULT 0,
                is_running INTEGER DEFAULT 0,
                last_error TEXT,
                chat_type TEXT DEFAULT 'REGULAR',
                e2ee_thread_id TEXT,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Cookie rotation history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cookie_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie_index INTEGER,
                cookie_preview TEXT,
                status TEXT,
                messages_sent INTEGER DEFAULT 0,
                error_message TEXT,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    
    def get_state(self) -> dict:
        """Get current automation state"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT current_cookie_index, current_message_index, current_haters_index,
                   total_messages_sent, is_running, last_error, chat_type, e2ee_thread_id
            FROM automation_state ORDER BY id DESC LIMIT 1
        ''')
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'current_cookie_index': result[0] or 0,
                'current_message_index': result[1] or 0,
                'current_haters_index': result[2] or 0,
                'total_messages_sent': result[3] or 0,
                'is_running': bool(result[4]),
                'last_error': result[5] or '',
                'chat_type': result[6] or 'REGULAR',
                'e2ee_thread_id': result[7] or ''
            }
        return {
            'current_cookie_index': 0,
            'current_message_index': 0,
            'current_haters_index': 0,
            'total_messages_sent': 0,
            'is_running': False,
            'last_error': '',
            'chat_type': 'REGULAR',
            'e2ee_thread_id': ''
        }
    
    def update_state(self, state: dict):
        """Update automation state"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Check if record exists
        cursor.execute('SELECT COUNT(*) FROM automation_state')
        count = cursor.fetchone()[0]
        
        if count == 0:
            cursor.execute('''
                INSERT INTO automation_state 
                (current_cookie_index, current_message_index, current_haters_index, 
                 total_messages_sent, is_running, last_error, chat_type, e2ee_thread_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                state.get('current_cookie_index', 0),
                state.get('current_message_index', 0),
                state.get('current_haters_index', 0),
                state.get('total_messages_sent', 0),
                1 if state.get('is_running') else 0,
                state.get('last_error', ''),
                state.get('chat_type', 'REGULAR'),
                state.get('e2ee_thread_id', '')
            ))
        else:
            cursor.execute('''
                UPDATE automation_state SET
                    current_cookie_index = ?,
                    current_message_index = ?,
                    current_haters_index = ?,
                    total_messages_sent = ?,
                    is_running = ?,
                    last_error = ?,
                    chat_type = ?,
                    e2ee_thread_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = (SELECT id FROM automation_state ORDER BY id DESC LIMIT 1)
            ''', (
                state.get('current_cookie_index', 0),
                state.get('current_message_index', 0),
                state.get('current_haters_index', 0),
                state.get('total_messages_sent', 0),
                1 if state.get('is_running') else 0,
                state.get('last_error', ''),
                state.get('chat_type', 'REGULAR'),
                state.get('e2ee_thread_id', '')
            ))
        
        conn.commit()
        conn.close()
    
    def log_cookie_rotation(self, cookie_index: int, cookie_preview: str, 
                           status: str, messages_sent: int = 0, error: str = ''):
        """Log cookie rotation events"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO cookie_history (cookie_index, cookie_preview, status, messages_sent, error_message)
            VALUES (?, ?, ?, ?, ?)
        ''', (cookie_index, cookie_preview[:50], status, messages_sent, error))
        
        conn.commit()
        conn.close()
    
    def increment_message_count(self, increment: int = 1):
        """Increment total messages sent"""
        state = self.get_state()
        state['total_messages_sent'] = state.get('total_messages_sent', 0) + increment
        self.update_state(state)
    
    def set_e2ee_thread_id(self, thread_id: str):
        """Save E2EE thread ID"""
        state = self.get_state()
        state['e2ee_thread_id'] = thread_id
        state['chat_type'] = 'E2EE'
        self.update_state(state)
    
    def get_e2ee_thread_id(self) -> str:
        """Get saved E2EE thread ID"""
        state = self.get_state()
        return state.get('e2ee_thread_id', '')


# ============================================================================
# FILE MANAGER
# ============================================================================

class FileManager:
    """Manages reading from configuration files"""
    
    @staticmethod
    def read_cookies() -> List[str]:
        """Read cookies from file, return list of cookie strings"""
        if not COOKIES_FILE.exists():
            logger.warning(f"Cookies file not found: {COOKIES_FILE}")
            return []
        
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        
        if not content:
            return []
        
        # Split by double newline for multiple cookie sets
        if '\n\n' in content:
            cookies = [c.strip() for c in content.split('\n\n') if c.strip()]
        else:
            cookies = [content]
        
        logger.info(f"Loaded {len(cookies)} cookie set(s)")
        return cookies
    
    @staticmethod
    def read_haters() -> List[str]:
        """Read haters names from file"""
        if not HATERS_FILE.exists():
            logger.warning(f"Haters file not found: {HATERS_FILE}")
            return ['']
        
        with open(HATERS_FILE, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
        
        return lines if lines else ['']
    
    @staticmethod
    def read_messages() -> List[str]:
        """Read messages from File.txt"""
        if not MESSAGES_FILE.exists():
            logger.warning(f"Messages file not found: {MESSAGES_FILE}")
            return ['Hello!']
        
        with open(MESSAGES_FILE, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
        
        return lines if lines else ['Hello!']
    
    @staticmethod
    def read_lastname() -> str:
        """Read last name from file"""
        if not LASTNAME_FILE.exists():
            return ''
        
        with open(LASTNAME_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()
    
    @staticmethod
    def read_delay() -> int:
        """Read delay from time.txt"""
        if not TIME_FILE.exists():
            return 30
        
        with open(TIME_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            try:
                return int(content)
            except ValueError:
                logger.warning(f"Invalid delay value: {content}, using default 30")
                return 30
    
    @staticmethod
    def read_thread_id() -> str:
        """Read thread ID from file"""
        if not THREAD_ID_FILE.exists():
            return ''
        
        with open(THREAD_ID_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()


# ============================================================================
# BROWSER MANAGER - WITH MEMORY CLEANUP
# ============================================================================

class BrowserManager:
    """Manages Chrome browser instance with Selenium"""
    
    def __init__(self):
        self.driver = None
    
    def setup_browser(self) -> webdriver.Chrome:
        """Setup Chrome browser with options - MEMORY OPTIMIZED"""
        chrome_options = Options()
        
        # CRITICAL: Headless mode with memory optimization
        chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-setuid-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--disable-plugins')
        chrome_options.add_argument('--disable-images')
        chrome_options.add_argument('--disable-javascript')
        chrome_options.add_argument('--disable-web-security')
        chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        chrome_options.add_argument('--disable-software-rasterizer')
        chrome_options.add_argument('--disable-logging')
        chrome_options.add_argument('--log-level=3')
        chrome_options.add_argument('--silent')
        chrome_options.add_argument('--window-size=1280,720')  # Smaller window = less memory
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
        
        # Memory optimization flags
        chrome_options.add_argument('--memory-pressure-off')
        chrome_options.add_argument('--max_old_space_size=256')
        chrome_options.add_argument('--js-flags="--max-old-space-size=256"')
        
        # Try to find Chrome/Chromium binary
        chromium_paths = [
            '/usr/bin/chromium',
            '/usr/bin/chromium-browser',
            '/usr/bin/google-chrome',
            '/usr/bin/chrome'
        ]
        
        for path in chromium_paths:
            if Path(path).exists():
                chrome_options.binary_location = path
                logger.info(f"Using browser binary: {path}")
                break
        
        # Try to find ChromeDriver
        chromedriver_paths = [
            '/usr/bin/chromedriver',
            '/usr/local/bin/chromedriver',
            '/opt/chromedriver/chromedriver'
        ]
        
        driver_path = None
        for path in chromedriver_paths:
            if Path(path).exists():
                driver_path = path
                logger.info(f"Using ChromeDriver: {path}")
                break
        
        try:
            if driver_path:
                service = Service(executable_path=driver_path)
                self.driver = webdriver.Chrome(service=service, options=chrome_options)
            else:
                self.driver = webdriver.Chrome(options=chrome_options)
            
            self.driver.set_window_size(1280, 720)
            logger.info("Browser setup completed with memory optimization")
            return self.driver
        except Exception as e:
            logger.error(f"Browser setup failed: {e}")
            raise
    
    def add_cookies(self, cookie_string: str):
        """Add cookies to browser"""
        if not cookie_string or not cookie_string.strip():
            return
        
        cookie_pairs = cookie_string.split(';')
        for cookie in cookie_pairs:
            cookie = cookie.strip()
            if not cookie:
                continue
            
            if '=' in cookie:
                name, value = cookie.split('=', 1)
                try:
                    self.driver.add_cookie({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.facebook.com',
                        'path': '/'
                    })
                except Exception as e:
                    logger.debug(f"Failed to add cookie {name}: {e}")
    
    def find_message_input(self, timeout: int = 30) -> Optional:
        """Find the message input element - SAME AS ORIGINAL main.py"""
        logger.info("Finding message input...")
        time.sleep(10)
        
        # Scroll to bottom and top to trigger UI
        try:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(2)
        except:
            pass
        
        # Get page info for debugging
        try:
            page_title = self.driver.title
            page_url = self.driver.current_url
            logger.info(f"Page Title: {page_title}")
            logger.info(f"Page URL: {page_url}")
        except Exception as e:
            logger.info(f"Could not get page info: {e}")
        
        # Message input selectors - EXACTLY SAME AS ORIGINAL
        message_input_selectors = [
            'div[contenteditable="true"][role="textbox"]',
            'div[contenteditable="true"][data-lexical-editor="true"]',
            'div[aria-label*="message" i][contenteditable="true"]',
            'div[aria-label*="Message" i][contenteditable="true"]',
            'div[contenteditable="true"][spellcheck="true"]',
            '[role="textbox"][contenteditable="true"]',
            'textarea[placeholder*="message" i]',
            'div[aria-placeholder*="message" i]',
            'div[data-placeholder*="message" i]',
            '[contenteditable="true"]',
            'textarea',
            'input[type="text"]'
        ]
        
        logger.info(f"Trying {len(message_input_selectors)} selectors...")
        
        for idx, selector in enumerate(message_input_selectors):
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                logger.info(f"Selector {idx+1}/{len(message_input_selectors)} \"{selector[:50]}...\" found {len(elements)} elements")
                
                for element in elements:
                    try:
                        is_editable = self.driver.execute_script("""
                            return arguments[0].contentEditable === 'true' || 
                                   arguments[0].tagName === 'TEXTAREA' || 
                                   arguments[0].tagName === 'INPUT';
                        """, element)
                        
                        if is_editable:
                            logger.info(f"Found editable element with selector #{idx+1}")
                            
                            try:
                                element.click()
                                time.sleep(0.5)
                            except:
                                pass
                            
                            element_text = self.driver.execute_script("return arguments[0].placeholder || arguments[0].getAttribute('aria-label') || arguments[0].getAttribute('aria-placeholder') || '';", element).lower()
                            
                            keywords = ['message', 'write', 'type', 'send', 'chat', 'msg', 'reply', 'text', 'aa']
                            if any(keyword in element_text for keyword in keywords):
                                logger.info(f"✅ Found message input with text: {element_text[:50]}")
                                return element
                            elif idx < 10:
                                logger.info(f"✅ Using primary selector editable element (#{idx+1})")
                                return element
                            elif selector == '[contenteditable="true"]' or selector == 'textarea' or selector == 'input[type="text"]':
                                logger.info(f"✅ Using fallback editable element")
                                return element
                    except Exception as e:
                        logger.info(f"Element check failed: {str(e)[:50]}")
                        continue
            except Exception as e:
                continue
        
        # Check page source for contenteditable
        try:
            page_source = self.driver.page_source
            logger.info(f"Page source length: {len(page_source)} characters")
            if 'contenteditable' in page_source.lower():
                logger.info("Page contains contenteditable elements")
            else:
                logger.info("No contenteditable elements found in page")
        except Exception:
            pass
        
        logger.error("Could not find message input")
        return None
    
    def send_message(self, message_input, message: str) -> bool:
        """Send a message using the message input element - SAME AS ORIGINAL"""
        try:
            # Set message text
            self.driver.execute_script("""
                const element = arguments[0];
                const message = arguments[1];
                
                element.scrollIntoView({behavior: 'smooth', block: 'center'});
                element.focus();
                element.click();
                
                if (element.tagName === 'DIV') {
                    element.textContent = message;
                    element.innerHTML = message;
                } else {
                    element.value = message;
                }
                
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
                element.dispatchEvent(new InputEvent('input', { bubbles: true, data: message }));
            """, message_input, message)
            
            time.sleep(1)
            
            # Try to click send button
            send_result = self.driver.execute_script("""
                const sendButtons = document.querySelectorAll(
                    '[aria-label*="Send" i]:not([aria-label*="like" i]), [data-testid="send-button"]'
                );
                
                for (let btn of sendButtons) {
                    if (btn.offsetParent !== null) {
                        btn.click();
                        return 'button_clicked';
                    }
                }
                return 'button_not_found';
            """)
            
            if send_result == 'button_not_found':
                logger.info("Send button not found, using Enter key...")
                self.driver.execute_script("""
                    const element = arguments[0];
                    element.focus();
                    
                    const events = [
                        new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }),
                        new KeyboardEvent('keypress', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }),
                        new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true })
                    ];
                    
                    events.forEach(event => element.dispatchEvent(event));
                """, message_input)
                logger.info(f"✅ Sent via Enter: \"{message[:30]}...\"")
            else:
                logger.info(f"✅ Sent via button: \"{message[:30]}...\"")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False
    
    def check_login(self) -> bool:
        """Check if user is logged into Facebook"""
        try:
            current_url = self.driver.current_url.lower()
            if 'login' in current_url or 'checkpoint' in current_url:
                return False
            
            # Check for login button or user menu
            has_login_form = self.driver.find_elements(By.CSS_SELECTOR, 'input[name="email"], input[name="pass"]')
            if has_login_form:
                return False
            
            return True
        except:
            return False
    
    def navigate_to_conversation(self, thread_id: str, chat_type: str = 'REGULAR') -> bool:
        """Navigate to a specific conversation - SUPPORTS BOTH REGULAR AND E2EE"""
        try:
            if chat_type == 'E2EE':
                conversation_url = f'https://www.facebook.com/messages/e2ee/t/{thread_id}'
                logger.info(f"Opening E2EE conversation: {conversation_url}")
            else:
                conversation_url = f'https://www.facebook.com/messages/t/{thread_id}'
                logger.info(f"Opening REGULAR conversation: {conversation_url}")
            
            self.driver.get(conversation_url)
            time.sleep(10)
            return True
        except Exception as e:
            logger.error(f"Failed to navigate: {e}")
            return False
    
    def detect_chat_type(self) -> str:
        """Detect if current chat is E2EE or regular"""
        try:
            current_url = self.driver.current_url.lower()
            if 'e2ee' in current_url:
                logger.info("Detected E2EE chat type")
                return 'E2EE'
            else:
                logger.info("Detected REGULAR chat type")
                return 'REGULAR'
        except:
            return 'REGULAR'
    
    def clear_memory(self):
        """Clear browser memory by navigating to blank page"""
        try:
            self.driver.get('about:blank')
            self.driver.execute_script("window.localStorage.clear();")
            self.driver.execute_script("window.sessionStorage.clear();")
            logger.info("Browser memory cleared")
        except:
            pass
    
    def close(self):
        """Close the browser and free memory"""
        if self.driver:
            try:
                # Clear memory before closing
                self.clear_memory()
                self.driver.quit()
                logger.info("Browser closed and memory freed")
            except:
                pass
            self.driver = None


# ============================================================================
# MESSAGE FORMATTER
# ============================================================================

class MessageFormatter:
    """Formats messages according to pattern: hatersname + messages + lastname"""
    
    def __init__(self):
        self.haters = []
        self.messages = []
        self.lastname = ''
        self.haters_index = 0
        self.messages_index = 0
        self.last_reload_time = 0
    
    def reload_data(self):
        """Reload data from files"""
        self.haters = FileManager.read_haters()
        self.messages = FileManager.read_messages()
        self.lastname = FileManager.read_lastname()
        self.last_reload_time = time.time()
        
        logger.info(f"Loaded {len(self.haters)} haters, {len(self.messages)} messages")
    
    def set_indices(self, haters_index: int, messages_index: int):
        """Set current indices for rotation"""
        self.haters_index = haters_index % len(self.haters) if self.haters else 0
        self.messages_index = messages_index % len(self.messages) if self.messages else 0
    
    def get_next_message(self) -> Tuple[str, int, int]:
        """Get next formatted message and update indices"""
        # Reload data every 100 messages to pick up changes
        if time.time() - self.last_reload_time > 300:  # Every 5 minutes
            self.reload_data()
        
        if not self.haters:
            self.reload_data()
        
        if not self.haters:
            hater = ''
        else:
            hater = self.haters[self.haters_index % len(self.haters)]
        
        if not self.messages:
            self.reload_data()
        
        if not self.messages:
            msg = 'Hello!'
        else:
            msg = self.messages[self.messages_index % len(self.messages)]
        
        # Format: hatersname + messages + lastname
        formatted = f"{hater}{msg}{self.lastname}"
        
        # Update indices
        current_hater_idx = self.haters_index
        current_msg_idx = self.messages_index
        
        self.haters_index += 1
        self.messages_index += 1
        
        return formatted, current_hater_idx, current_msg_idx


# ============================================================================
# AUTOMATION ENGINE - WITH MEMORY MANAGEMENT
# ============================================================================

class AutomationEngine:
    """Main automation engine that runs the message sending loop"""
    
    def __init__(self):
        self.is_running = False
        self.should_stop = False
        self.db = DatabaseManager()
        self.browser = BrowserManager()
        self.formatter = MessageFormatter()
        self.current_cookie_index = 0
        self.cookies = []
        self.total_messages_sent = 0
        self.chat_type = 'REGULAR'
        self.e2ee_thread_id = ''
        
        # MEMORY MANAGEMENT SETTINGS
        self.messages_per_session = 30  # Har 30 messages baad browser restart
        self.current_session_messages = 0
        
        # Load initial state
        state = self.db.get_state()
        self.current_cookie_index = state.get('current_cookie_index', 0)
        self.formatter.set_indices(
            state.get('current_haters_index', 0),
            state.get('current_message_index', 0)
        )
        self.total_messages_sent = state.get('total_messages_sent', 0)
        self.chat_type = state.get('chat_type', 'REGULAR')
        self.e2ee_thread_id = state.get('e2ee_thread_id', '')
    
    def reload_cookies(self):
        """Reload cookies from file"""
        self.cookies = FileManager.read_cookies()
        logger.info(f"Loaded {len(self.cookies)} cookie set(s)")
        
        if not self.cookies:
            logger.error("No cookies found! Please add cookies to cookies.txt")
            return False
        
        # Ensure index is within range
        if self.current_cookie_index >= len(self.cookies):
            self.current_cookie_index = 0
        
        return True
    
    def try_login_with_cookie(self, cookie_string: str) -> bool:
        """Attempt to login using cookies"""
        logger.info("Attempting login with cookies...")
        
        try:
            self.browser.driver.get('https://www.facebook.com/')
            time.sleep(5)
            
            # Clear existing cookies
            self.browser.driver.delete_all_cookies()
            
            # Add new cookies
            self.browser.add_cookies(cookie_string)
            
            # Refresh to apply cookies
            self.browser.driver.refresh()
            time.sleep(5)
            
            # Check if login was successful
            if self.browser.check_login():
                logger.info("Login successful with cookies")
                return True
            
            logger.warning("Login failed - cookies may be expired")
            return False
            
        except Exception as e:
            logger.error(f"Login attempt failed: {e}")
            return False
    
    def detect_thread_type_and_navigate(self, thread_id: str) -> Tuple[bool, str]:
        """Detect if thread is E2EE or regular and navigate accordingly"""
        # First try with regular format
        logger.info(f"Attempting to open REGULAR conversation: {thread_id}")
        if self.browser.navigate_to_conversation(thread_id, 'REGULAR'):
            time.sleep(5)
            
            # Check if we're on E2EE page
            if 'e2ee' in self.browser.driver.current_url.lower():
                logger.info("Detected E2EE conversation, switching to E2EE mode")
                # Extract E2EE thread ID from URL
                current_url = self.browser.driver.current_url
                if '/e2ee/t/' in current_url:
                    e2ee_id = current_url.split('/e2ee/t/')[-1].split('?')[0].split('/')[0]
                    logger.info(f"Found E2EE thread ID: {e2ee_id}")
                    self.e2ee_thread_id = e2ee_id
                    self.db.set_e2ee_thread_id(e2ee_id)
                    self.chat_type = 'E2EE'
                    return True, 'E2EE'
            
            # Check for message input to verify it's a valid chat
            msg_input = self.browser.find_message_input()
            if msg_input:
                logger.info("Regular chat opened successfully")
                return True, 'REGULAR'
        
        # If regular failed, try E2EE format
        if self.e2ee_thread_id:
            logger.info(f"Attempting to open saved E2EE conversation: {self.e2ee_thread_id}")
            if self.browser.navigate_to_conversation(self.e2ee_thread_id, 'E2EE'):
                time.sleep(5)
                msg_input = self.browser.find_message_input()
                if msg_input:
                    logger.info("E2EE chat opened successfully")
                    self.chat_type = 'E2EE'
                    return True, 'E2EE'
        
        return False, 'REGULAR'
    
    def run_session_with_cookie(self, cookie_string: str, cookie_index: int) -> Tuple[bool, int]:
        """
        Run ONE SESSION with a specific cookie
        Returns: (should_continue_with_same_cookie, messages_sent)
        """
        messages_sent = 0
        thread_id = FileManager.read_thread_id()
        delay = FileManager.read_delay()
        
        if not thread_id:
            logger.error("No thread ID found in thread_id.txt")
            return False, 0
        
        cookie_preview = cookie_string[:50] + "..." if len(cookie_string) > 50 else cookie_string
        logger.info(f"=== Starting session with Cookie #{cookie_index + 1}: {cookie_preview} ===")
        
        try:
            # Setup fresh browser for this session
            self.browser.setup_browser()
            
            # Login with cookie
            self.browser.driver.get('https://www.facebook.com/')
            time.sleep(5)
            self.browser.add_cookies(cookie_string)
            self.browser.driver.refresh()
            time.sleep(5)
            
            # Check login status
            if not self.browser.check_login():
                logger.error(f"Cookie #{cookie_index + 1} login failed - invalid or expired")
                self.db.log_cookie_rotation(cookie_index, cookie_preview, 'failed', 0, 'Login failed')
                return False, 0
            
            logger.info(f"Cookie #{cookie_index + 1} login successful")
            
            # Navigate to conversation
            success, detected_type = self.detect_thread_type_and_navigate(thread_id)
            
            if not success:
                logger.error("Failed to navigate to conversation")
                return False, 0
            
            logger.info(f"Chat type: {detected_type}")
            
            # Find message input
            message_input = self.browser.find_message_input()
            if not message_input:
                logger.error("Message input not found - possible logout")
                return False, 0
            
            logger.info("Message input found, starting message loop")
            
            # Send messages in this session
            while self.is_running and not self.should_stop and self.current_session_messages < self.messages_per_session:
                try:
                    # Reload data periodically
                    if messages_sent % 10 == 0:
                        self.formatter.reload_data()
                        delay = FileManager.read_delay()
                        new_thread_id = FileManager.read_thread_id()
                        
                        # Check if thread ID changed
                        if new_thread_id != thread_id:
                            logger.info(f"Thread ID changed from {thread_id} to {new_thread_id}")
                            thread_id = new_thread_id
                            success, detected_type = self.detect_thread_type_and_navigate(thread_id)
                            if not success:
                                logger.error("Failed to navigate to new conversation")
                                return True, messages_sent
                            time.sleep(5)
                            message_input = self.browser.find_message_input()
                            if not message_input:
                                return True, messages_sent
                    
                    # Check cookie validity every 10 messages
                    if messages_sent > 0 and messages_sent % 10 == 0:
                        if not self.browser.check_login():
                            logger.warning("Cookie appears to be expired during session")
                            return False, messages_sent
                    
                    # Get formatted message
                    message, hater_idx, msg_idx = self.formatter.get_next_message()
                    
                    if not message.strip():
                        message = "Hello!"
                    
                    logger.info(f"Message #{self.total_messages_sent + messages_sent + 1}: {message[:50]}...")
                    
                    # Send the message
                    success = self.browser.send_message(message_input, message)
                    
                    if not success:
                        logger.warning("Failed to send message, checking if still logged in")
                        if not self.browser.check_login():
                            logger.warning("Session appears to be logged out")
                            return False, messages_sent
                    
                    messages_sent += 1
                    self.current_session_messages += 1
                    self.total_messages_sent += 1
                    
                    # Save state
                    self.db.update_state({
                        'current_cookie_index': self.current_cookie_index,
                        'current_haters_index': self.formatter.haters_index,
                        'current_message_index': self.formatter.messages_index,
                        'total_messages_sent': self.total_messages_sent,
                        'is_running': True,
                        'last_error': '',
                        'chat_type': self.chat_type,
                        'e2ee_thread_id': self.e2ee_thread_id
                    })
                    
                    logger.info(f"✅ Message sent successfully. ({self.current_session_messages}/{self.messages_per_session} in this session)")
                    
                    # Wait for delay
                    for _ in range(delay):
                        if self.should_stop or not self.is_running:
                            return True, messages_sent
                        time.sleep(1)
                    
                except WebDriverException as e:
                    logger.error(f"WebDriver error: {e}")
                    return False, messages_sent
                except Exception as e:
                    logger.error(f"Unexpected error in message loop: {e}")
                    logger.debug(traceback.format_exc())
                    time.sleep(5)
            
            # Session completed successfully
            if self.current_session_messages >= self.messages_per_session:
                logger.info(f"Session completed: {messages_sent} messages sent with Cookie #{cookie_index + 1}")
                return True, messages_sent  # Continue with same cookie
            else:
                return False, messages_sent  # Stop automation
            
        except Exception as e:
            logger.error(f"Error in session: {e}")
            logger.debug(traceback.format_exc())
            return False, messages_sent
        finally:
            # CRITICAL: Close browser to free memory after session
            self.browser.close()
            logger.info(f"Session ended. Browser closed, memory freed.")
    
    def run(self):
        """Main automation loop with proper session management"""
        logger.info("=" * 60)
        logger.info("R4J M1SHR4 Automation Bot Starting... (MEMORY FIXED VERSION)")
        logger.info("=" * 60)
        
        self.is_running = True
        self.should_stop = False
        
        # Load initial data
        self.formatter.reload_data()
        
        if not self.reload_cookies():
            logger.error("No cookies found. Exiting.")
            return
        
        # Main loop - run sessions
        while self.is_running and not self.should_stop:
            try:
                # Get current cookie
                if self.current_cookie_index >= len(self.cookies):
                    self.current_cookie_index = 0
                
                cookie_string = self.cookies[self.current_cookie_index]
                
                # Reset session counter for this cookie
                self.current_session_messages = 0
                
                # Run a session with this cookie
                continue_with_same, messages_sent = self.run_session_with_cookie(cookie_string, self.current_cookie_index)
                
                if messages_sent > 0:
                    # Log successful cookie usage
                    cookie_preview = cookie_string[:50] + "..." if len(cookie_string) > 50 else cookie_string
                    self.db.log_cookie_rotation(
                        self.current_cookie_index,
                        cookie_preview,
                        'success',
                        messages_sent,
                        ''
                    )
                
                if continue_with_same and messages_sent > 0:
                    # Cookie still valid, continue with same cookie
                    logger.info(f"Cookie #{self.current_cookie_index + 1} still valid, continuing...")
                    time.sleep(5)
                    continue
                else:
                    # Cookie expired or failed, move to next cookie
                    if messages_sent == 0:
                        logger.warning(f"Cookie #{self.current_cookie_index + 1} failed, moving to next")
                    
                    self.current_cookie_index = (self.current_cookie_index + 1) % len(self.cookies)
                    self.db.update_state({
                        'current_cookie_index': self.current_cookie_index,
                        'current_haters_index': self.formatter.haters_index,
                        'current_message_index': self.formatter.messages_index,
                        'total_messages_sent': self.total_messages_sent,
                        'is_running': True,
                        'last_error': f'Switched to cookie #{self.current_cookie_index + 1}',
                        'chat_type': self.chat_type,
                        'e2ee_thread_id': self.e2ee_thread_id
                    })
                    
                    logger.info(f"Switching to cookie #{self.current_cookie_index + 1}")
                    time.sleep(10)
                
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                break
            except Exception as e:
                logger.error(f"Fatal error in main loop: {e}")
                logger.debug(traceback.format_exc())
                time.sleep(30)
        
        self.is_running = False
        self.db.update_state({
            'current_cookie_index': self.current_cookie_index,
            'current_haters_index': self.formatter.haters_index,
            'current_message_index': self.formatter.messages_index,
            'total_messages_sent': self.total_messages_sent,
            'is_running': False,
            'last_error': 'Automation stopped',
            'chat_type': self.chat_type,
            'e2ee_thread_id': self.e2ee_thread_id
        })
        
        logger.info("=" * 60)
        logger.info(f"Automation stopped. Total messages sent: {self.total_messages_sent}")
        logger.info("=" * 60)
    
    def stop(self):
        """Stop the automation"""
        logger.info("Stopping automation...")
        self.should_stop = True
        self.is_running = False
        
        # Close browser if open
        try:
            self.browser.close()
        except:
            pass


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point"""
    # Check for required files
    required_files = [
        ('cookies.txt', 'Add your Facebook cookies'),
        ('thread_id.txt', 'Add your conversation/thread ID'),
    ]
    
    missing_files = []
    for filename, message in required_files:
        if not (BASE_DIR / filename).exists():
            missing_files.append(f"{filename} - {message}")
    
    if missing_files:
        logger.error("Missing required files:")
        for mf in missing_files:
            logger.error(f"  - {mf}")
        logger.error("\nPlease create these files before running.")
        logger.error(f"Working directory: {BASE_DIR}")
        sys.exit(1)
    
    # Optional files check
    optional_files = [
        ('hatersname.txt', 'Will use empty string if missing'),
        ('File.txt', 'Will use default message if missing'),
        ('time.txt', 'Will use 30 seconds if missing'),
        ('lastname.txt', 'Will use empty string if missing'),
    ]
    
    for filename, message in optional_files:
        if not (BASE_DIR / filename).exists():
            logger.info(f"Note: {filename} not found. {message}")
            # Create empty file if needed
            if filename in ['hatersname.txt', 'lastname.txt']:
                (BASE_DIR / filename).touch()
    
    # Create default messages file if needed
    if not (BASE_DIR / 'File.txt').exists():
        with open(BASE_DIR / 'File.txt', 'w', encoding='utf-8') as f:
            f.write("Hello!\nHow are you?\nGood morning!")
        logger.info("Created default File.txt with sample messages")
    
    # Create default time.txt if needed
    if not (BASE_DIR / 'time.txt').exists():
        with open(BASE_DIR / 'time.txt', 'w', encoding='utf-8') as f:
            f.write("30")
        logger.info("Created default time.txt with delay 30 seconds")
    
    # Run automation
    logger.info("Starting automation...")
    logger.info(f"Cookies: {len(FileManager.read_cookies())} set(s)")
    logger.info(f"Thread ID: {FileManager.read_thread_id()}")
    logger.info(f"Delay: {FileManager.read_delay()} seconds")
    logger.info(f"Messages per session: 30 (auto-restart for memory management)")
    
    engine = AutomationEngine()
    
    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("Stopping...")
        engine.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.debug(traceback.format_exc())
        engine.stop()


if __name__ == "__main__":
    main()
