import requests
import pandas as pd
from datetime import datetime
from io import StringIO
import pytz
import os
import sys
import tempfile
import shutil
import logging
from pathlib import Path
from typing import Optional
import json

# ==================== CONFIGURATION ====================
class Config:
    """Configuration settings for the market data scraper."""
    TIMEZONE = "Asia/Karachi"
    BASE_DIR = "data"
    LOG_DIR = "logs"
    
    # URLs
    MAIN_URL = "https://dps.psx.com.pk/"
    DATA_URL = "https://dps.psx.com.pk/market-watch"
    
    # Request settings
    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    
    # Data columns
    EXPECTED_COLUMNS = [
        "SYMBOL", "SECTOR", "LDCP", "OPEN", "HIGH", "LOW",
        "CURRENT", "CHANGE", "CHANGE (%)", "VOLUME"
    ]
    
    NUMERIC_COLUMNS = [
        "LDCP", "OPEN", "HIGH", "LOW",
        "CURRENT", "CHANGE", "CHANGE (%)", "VOLUME"
    ]
    
    REMOVE_SYMBOLS = {"XT", "XD", "XR", "XS"}

# ==================== LOGGING SETUP ====================
class LoggerSetup:
    """Setup logging configuration with file and console handlers."""
    
    @staticmethod
    def setup_logger(name: str = "MarketDataScraper") -> logging.Logger:
        """
        Configure and return a logger with both file and console handlers.
        
        Args:
            name: Logger name
            
        Returns:
            Configured logger instance
        """
        # Create logs directory
        log_dir = Path(Config.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create logger
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        
        # Avoid duplicate handlers
        if logger.handlers:
            return logger
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(funcName)-20s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # File handler (daily rotation)
        pkt = pytz.timezone(Config.TIMEZONE)
        today = datetime.now(pkt).strftime("%Y-%m-%d")
        log_file = log_dir / f"scraper_{today}.log"
        
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        
        # Add handlers
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        return logger

# ==================== DATA FETCHER ====================
class MarketDataFetcher:
    """Handle HTTP requests and data retrieval from PSX."""
    
    def __init__(self, logger: logging.Logger):
        """Initialize fetcher with logger and session."""
        self.logger = logger
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/html",
            "X-Requested-With": "XMLHttpRequest",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://dps.psx.com.pk/"
        }
    
    def fetch_data(self) -> Optional[requests.Response]:
        """
        Fetch market data with retry logic.
        
        Returns:
            Response object or None if failed
        """
        for attempt in range(1, Config.MAX_RETRIES + 1):
            try:
                self.logger.info(f"Fetching data (Attempt {attempt}/{Config.MAX_RETRIES})...")
                
                # Initial request to establish session
                self.logger.debug(f"Establishing session with {Config.MAIN_URL}")
                self.session.get(
                    Config.MAIN_URL,
                    headers=self.headers,
                    timeout=Config.REQUEST_TIMEOUT
                )
                
                # Small delay between requests
                import time
                time.sleep(1)
                
                # Actual data request
                self.logger.debug(f"Requesting data from {Config.DATA_URL}")
                response = self.session.get(
                    Config.DATA_URL,
                    headers=self.headers,
                    timeout=Config.REQUEST_TIMEOUT
                )
                response.raise_for_status()
                
                # Log response details
                self.logger.debug(f"Response status: {response.status_code}")
                self.logger.debug(f"Response content type: {response.headers.get('Content-Type')}")
                self.logger.debug(f"Response size: {len(response.content)} bytes")
                
                self.logger.info(f"✅ Data fetched successfully (Status: {response.status_code})")
                return response
                
            except requests.Timeout:
                self.logger.warning(f"⚠️ Request timeout (Attempt {attempt})")
            except requests.ConnectionError as e:
                self.logger.warning(f"⚠️ Connection error (Attempt {attempt}): {e}")
            except requests.HTTPError as e:
                self.logger.error(f"❌ HTTP error: {e}")
                if e.response is not None:
                    self.logger.error(f"Response content: {e.response.text[:500]}")
                break
            except Exception as e:
                self.logger.error(f"❌ Unexpected error: {e}", exc_info=True)
                break
            
            if attempt < Config.MAX_RETRIES:
                import time
                self.logger.info(f"⏳ Retrying in {Config.RETRY_DELAY} seconds...")
                time.sleep(Config.RETRY_DELAY)
        
        self.logger.error("❌ Failed to fetch data after all retries")
        return None

# ==================== DATA PROCESSOR ====================
class DataProcessor:
    """Process and clean market data."""
    
    def __init__(self, logger: logging.Logger):
        """Initialize processor with logger."""
        self.logger = logger
    
    def parse_json(self, response: requests.Response) -> Optional[pd.DataFrame]:
        """
        Parse JSON response into DataFrame.
        
        Args:
            response: HTTP response object
            
        Returns:
            DataFrame or None if parsing failed
        """
        try:
            self.logger.debug("Attempting JSON parsing...")
            
            # Log first 500 characters of response
            response_preview = response.text[:500]
            self.logger.debug(f"Response preview: {response_preview}")
            
            data = response.json()
            
            if not isinstance(data, list):
                raise ValueError(f"Unexpected response type: {type(data)}, expected list")
            
            if not data:
                raise ValueError("Empty data array received")
            
            self.logger.debug(f"Received {len(data)} records")
            self.logger.debug(f"First record structure: {data[0] if data else 'N/A'}")
            
            if not isinstance(data[0], (list, tuple)):
                raise ValueError(f"Unexpected row structure: {type(data[0])}")
            
            if len(data[0]) < 10:
                raise ValueError(f"Insufficient columns: {len(data[0])}, expected at least 10")
            
            rows = []
            for idx, stock in enumerate(data):
                try:
                    rows.append({
                        "SYMBOL": stock[0],
                        "SECTOR": stock[1],
                        "LDCP": stock[2],
                        "OPEN": stock[3],
                        "HIGH": stock[4],
                        "LOW": stock[5],
                        "CURRENT": stock[6],
                        "CHANGE": stock[7],
                        "CHANGE (%)": stock[8],
                        "VOLUME": stock[9],
                    })
                except (IndexError, KeyError) as e:
                    self.logger.warning(f"Skipping row {idx} due to error: {e}")
                    continue
            
            if not rows:
                raise ValueError("No valid rows extracted from data")
            
            df = pd.DataFrame(rows)
            self.logger.info(f"✅ JSON parsed successfully ({len(df)} rows)")
            return df
            
        except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            self.logger.warning(f"⚠️ JSON parsing failed: {e}")
            return None
    
    def parse_html(self, response: requests.Response) -> Optional[pd.DataFrame]:
        """
        Parse HTML response as fallback.
        
        Args:
            response: HTTP response object
            
        Returns:
            DataFrame or None if parsing failed
        """
        try:
            self.logger.info("Attempting HTML fallback parsing...")
            
            tables = pd.read_html(StringIO(response.text))
            
            if not tables:
                raise ValueError("No tables found in HTML")
            
            self.logger.debug(f"Found {len(tables)} table(s) in HTML")
            
            df = tables[0]
            self.logger.debug(f"Table columns: {df.columns.tolist()}")
            self.logger.debug(f"Table shape: {df.shape}")
            
            self.logger.info(f"✅ HTML parsed successfully ({len(df)} rows)")
            return df
            
        except Exception as e:
            self.logger.error(f"❌ HTML parsing failed: {e}", exc_info=True)
            return None
    
    def clean_data(self, df: pd.DataFrame, timestamp: datetime) -> pd.DataFrame:
        """
        Clean and enrich DataFrame.
        
        Args:
            df: Raw DataFrame
            timestamp: Current timestamp
            
        Returns:
            Cleaned DataFrame
        """
        self.logger.info("Cleaning data...")
        
        # Log initial state
        self.logger.debug(f"Initial shape: {df.shape}")
        self.logger.debug(f"Initial columns: {df.columns.tolist()}")
        
        # Remove unwanted columns
        if "LISTED IN" in df.columns:
            df.drop(columns=["LISTED IN"], inplace=True)
            self.logger.debug("Removed 'LISTED IN' column")
        
        # Remove specific symbols
        initial_count = len(df)
        df = df[~df["SYMBOL"].isin(Config.REMOVE_SYMBOLS)]
        removed_count = initial_count - len(df)
        if removed_count > 0:
            self.logger.debug(f"Removed {removed_count} unwanted symbols")
        
        # Convert numeric columns
        for col in Config.NUMERIC_COLUMNS:
            if col in df.columns:
                original_dtype = df[col].dtype
                df[col] = pd.to_numeric(df[col], errors="coerce")
                self.logger.debug(f"Converted {col} from {original_dtype} to numeric")
        
        # Add timestamp columns
        df["YEAR"] = timestamp.year
        df["MONTH"] = timestamp.month
        df["DAY"] = timestamp.day
        df["HOUR"] = timestamp.hour
        df["MINUTE"] = timestamp.minute
        df["DATETIME"] = timestamp.strftime("%Y-%m-%d %H:%M")
        
        # Reorder columns
        final_columns = [
            "SYMBOL", "SECTOR",
            "LDCP", "OPEN", "HIGH", "LOW",
            "CURRENT", "CHANGE", "CHANGE (%)", "VOLUME",
            "YEAR", "MONTH", "DAY",
            "HOUR", "MINUTE", "DATETIME",
        ]
        
        # Keep only existing columns in the desired order
        df = df[[col for col in final_columns if col in df.columns]]
        
        self.logger.info(f"✅ Data cleaned ({len(df)} rows, {len(df.columns)} columns)")
        self.logger.debug(f"Final columns: {df.columns.tolist()}")
        
        return df

# ==================== DATA STORAGE ====================
class DataStorage:
    """Handle data persistence to Excel files."""
    
    def __init__(self, logger: logging.Logger):
        """Initialize storage with logger."""
        self.logger = logger
    
    def get_file_path(self, timestamp: datetime) -> Path:
        """
        Generate file path based on timestamp.
        
        Args:
            timestamp: Current timestamp
            
        Returns:
            Path object for the Excel file
        """
        year = str(timestamp.year)
        month = f"{timestamp.month:02d}"
        day = f"{timestamp.day:02d}"
        
        folder_path = Path(Config.BASE_DIR) / year / month
        folder_path.mkdir(parents=True, exist_ok=True)
        
        file_name = f"{year}-{month}-{day}.xlsx"
        file_path = folder_path / file_name
        
        self.logger.debug(f"Generated file path: {file_path}")
        return file_path
    
    def save_data(self, df: pd.DataFrame, file_path: Path) -> bool:
        """
        Save DataFrame to Excel with deduplication.
        
        Args:
            df: DataFrame to save
            file_path: Destination file path
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.info(f"Saving data to: {file_path}")
            
            # Load and merge with existing data
            if file_path.exists():
                self.logger.debug("Existing file found, merging data...")
                try:
                    old_df = pd.read_excel(file_path, engine='openpyxl')
                    self.logger.debug(f"Loaded {len(old_df)} existing rows")
                    
                    df = pd.concat([old_df, df], ignore_index=True)
                    self.logger.debug(f"Combined to {len(df)} rows")
                    
                    # Deduplicate
                    initial_count = len(df)
                    df.drop_duplicates(
                        subset=["SYMBOL", "DATETIME"],
                        keep="last",
                        inplace=True
                    )
                    duplicates_removed = initial_count - len(df)
                    if duplicates_removed > 0:
                        self.logger.debug(f"Removed {duplicates_removed} duplicate rows")
                
                except Exception as e:
                    self.logger.warning(f"Could not read existing file: {e}")
                    self.logger.info("Will create new file")
            
            # Atomic write using temporary file
            self.logger.debug("Writing to temporary file...")
            with tempfile.NamedTemporaryFile(
                dir=file_path.parent,
                suffix=".xlsx",
                delete=False
            ) as tmp:
                tmp_path = Path(tmp.name)
            
            # Write data
            df.to_excel(tmp_path, index=False, engine='openpyxl')
            self.logger.debug(f"Data written to temp file: {tmp_path}")
            
            # Move temp file to final location
            shutil.move(str(tmp_path), str(file_path))
            self.logger.debug("Temp file moved to final location")
            
            # Verify file
            if file_path.exists():
                file_size = file_path.stat().st_size
                self.logger.info(f"✅ Data saved successfully ({len(df)} total rows, {file_size} bytes)")
                return True
            else:
                self.logger.error("❌ File verification failed - file does not exist")
                return False
            
        except PermissionError:
            self.logger.error(f"❌ Permission denied: {file_path} (file may be open)")
            return False
        except Exception as e:
            self.logger.error(f"❌ Save failed: {e}", exc_info=True)
            
            # Cleanup temp file if it exists
            try:
                if 'tmp_path' in locals() and tmp_path.exists():
                    tmp_path.unlink()
                    self.logger.debug("Cleaned up temp file")
            except:
                pass
            
            return False

# ==================== MAIN SCRAPER ====================
class MarketDataScraper:
    """Main orchestrator for market data scraping - single execution."""
    
    def __init__(self):
        """Initialize scraper with all components."""
        self.logger = LoggerSetup.setup_logger()
        self.fetcher = MarketDataFetcher(self.logger)
        self.processor = DataProcessor(self.logger)
        self.storage = DataStorage(self.logger)
        self.timezone = pytz.timezone(Config.TIMEZONE)
    
    def run(self) -> bool:
        """
        Execute single scraping operation.
        
        Returns:
            True if successful, False otherwise
        """
        self.logger.info("=" * 70)
        self.logger.info("🚀 MARKET DATA SCRAPER - SINGLE EXECUTION")
        self.logger.info("=" * 70)
        
        timestamp = datetime.now(self.timezone)
        self.logger.info(f"Execution Time: {timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        self.logger.info(f"Configuration:")
        self.logger.info(f"   - Timezone: {Config.TIMEZONE}")
        self.logger.info(f"   - Data directory: {Config.BASE_DIR}")
        self.logger.info(f"   - Log directory: {Config.LOG_DIR}")
        self.logger.info("=" * 70)
        
        try:
            # Fetch data
            response = self.fetcher.fetch_data()
            if not response:
                self.logger.error("❌ Execution failed: Unable to fetch data")
                return False
            
            # Parse data (try JSON first, then HTML)
            df = self.processor.parse_json(response)
            if df is None:
                self.logger.info("JSON parsing failed, trying HTML fallback...")
                df = self.processor.parse_html(response)
            
            if df is None or df.empty:
                self.logger.warning("⚠️ No data retrieved (market may be closed)")
                return False
            
            # Clean data
            df = self.processor.clean_data(df, timestamp)
            
            # Save data
            file_path = self.storage.get_file_path(timestamp)
            success = self.storage.save_data(df, file_path)
            
            if success:
                self.logger.info("=" * 70)
                self.logger.info(f"📊 EXECUTION SUMMARY:")
                self.logger.info(f"   ✅ Status: SUCCESS")
                self.logger.info(f"   - Rows processed: {len(df)}")
                self.logger.info(f"   - Unique symbols: {df['SYMBOL'].nunique()}")
                self.logger.info(f"   - Timestamp: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
                self.logger.info(f"   - File: {file_path}")
                self.logger.info("=" * 70)
                
                # Sample data
                if len(df) > 0:
                    self.logger.debug("Sample data (first row):")
                    self.logger.debug(df.iloc[0].to_dict())
            else:
                self.logger.error("=" * 70)
                self.logger.error(f"📊 EXECUTION SUMMARY:")
                self.logger.error(f"   ❌ Status: FAILED")
                self.logger.error(f"   - Could not save data to file")
                self.logger.error("=" * 70)
            
            return success
            
        except KeyboardInterrupt:
            self.logger.warning("\n⚠️ Script interrupted by user")
            return False
        except Exception as e:
            self.logger.error(f"❌ Unexpected error during execution: {e}", exc_info=True)
            return False

# ==================== ENTRY POINT ====================
if __name__ == "__main__":
    # Initial debug information
    print("=" * 70)
    print("PSX Market Data Collector - Single Execution Mode")
    print("=" * 70)
    print(f"Python Version: {sys.version}")
    print(f"Current Directory: {os.getcwd()}")
    print(f"Script Location: {os.path.abspath(__file__)}")
    print(f"Time: {datetime.now()}")
    print("=" * 70)
    print()
    
    try:
        scraper = MarketDataScraper()
        success = scraper.run()
        
        if success:
            print("\n✅ Scraper completed successfully!")
            sys.exit(0)
        else:
            print("\n❌ Scraper completed with errors!")
            sys.exit(1)
            
    except Exception as e:
        print(f"\n{'='*70}")
        print(f"❌ FATAL ERROR")
        print(f"{'='*70}")
        print(f"Error: {e}")
        print(f"\nFull traceback:")
        import traceback
        traceback.print_exc()
        print(f"{'='*70}")
        sys.exit(1)
