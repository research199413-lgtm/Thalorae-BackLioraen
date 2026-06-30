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
import argparse

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
    SESSION_DELAY = 1  # Delay between session establishment and data request
    
    # Data columns
    EXPECTED_COLUMNS = [
        "SYMBOL", "SECTOR", "LDCP", "OPEN", "HIGH", "LOW",
        "CURRENT", "CHANGE", "CHANGE (%)", "VOLUME"
    ]
    
    NUMERIC_COLUMNS = [
        "LDCP", "OPEN", "HIGH", "LOW",
        "CURRENT", "CHANGE", "CHANGE (%)", "VOLUME"
    ]
    
    # Symbols to exclude (FIXED: Moved from module level)
    REMOVE_SYMBOLS = {"XT", "XD", "XR", "XS"}
    
    # File settings
    MAX_BACKUP_FILES = 5  # Keep last N backup files

# ==================== DEPENDENCY CHECKER ====================
class DependencyChecker:
    """Check if required packages are installed."""
    
    REQUIRED_PACKAGES = {
        'requests': 'requests',
        'pandas': 'pandas',
        'pytz': 'pytz',
        'openpyxl': 'openpyxl'
    }
    
    @classmethod
    def check_dependencies(cls) -> tuple[bool, list[str]]:
        """
        Check if all required packages are available.
        
        Returns:
            Tuple of (all_available, missing_packages)
        """
        missing = []
        for package_name, import_name in cls.REQUIRED_PACKAGES.items():
            try:
                __import__(import_name)
            except ImportError:
                missing.append(package_name)
        
        return (len(missing) == 0, missing)

# ==================== LOGGING SETUP ====================
class LoggerSetup:
    """Setup logging configuration with file and console handlers."""
    
    @staticmethod
    def setup_logger(name: str = "MarketDataScraper", verbose: bool = False) -> logging.Logger:
        """
        Configure and return a logger with both file and console handlers.
        
        Args:
            name: Logger name
            verbose: If True, console shows DEBUG level
            
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
        console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
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
            "Referer": Config.MAIN_URL
        }
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close session."""
        self.close()
    
    def close(self):
        """Close the session properly."""
        if self.session:
            self.session.close()
            self.logger.debug("Session closed")
    
    def fetch_data(self) -> Optional[requests.Response]:
        """
        Fetch market data with retry logic.
        
        Returns:
            Response object or None if failed
        """
        import time
        
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
                time.sleep(Config.SESSION_DELAY)
                
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
            skipped_rows = 0
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
                    skipped_rows += 1
                    self.logger.warning(f"Skipping row {idx} due to error: {e}")
                    continue
            
            if skipped_rows > 0:
                self.logger.info(f"Skipped {skipped_rows} invalid rows")
            
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
        
        # Remove specific symbols (FIXED: Using Config.REMOVE_SYMBOLS)
        initial_count = len(df)
        df = df[~df["SYMBOL"].isin(Config.REMOVE_SYMBOLS)]
        removed_count = initial_count - len(df)
        if removed_count > 0:
            self.logger.debug(f"Removed {removed_count} unwanted symbols: {Config.REMOVE_SYMBOLS}")
        
        # Convert numeric columns
        for col in Config.NUMERIC_COLUMNS:
            if col in df.columns:
                original_dtype = df[col].dtype
                df[col] = pd.to_numeric(df[col], errors="coerce")
                # Count NaN values introduced by conversion
                nan_count = df[col].isna().sum()
                if nan_count > 0:
                    self.logger.warning(f"Column {col}: {nan_count} values could not be converted to numeric")
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
    
    def __init__(self, logger: logging.Logger, create_backup: bool = True):
        """
        Initialize storage with logger.
        
        Args:
            logger: Logger instance
            create_backup: Whether to create backup files
        """
        self.logger = logger
        self.create_backup = create_backup
    
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
    
    def _create_backup(self, file_path: Path) -> None:
        """
        Create backup of existing file.
        
        Args:
            file_path: File to backup
        """
        if not file_path.exists():
            return
        
        try:
            backup_dir = file_path.parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{file_path.stem}_backup_{timestamp}.xlsx"
            backup_path = backup_dir / backup_name
            
            shutil.copy2(file_path, backup_path)
            self.logger.debug(f"Backup created: {backup_path}")
            
            # Clean old backups
            self._cleanup_old_backups(backup_dir, file_path.stem)
            
        except Exception as e:
            self.logger.warning(f"Could not create backup: {e}")
    
    def _cleanup_old_backups(self, backup_dir: Path, file_stem: str) -> None:
        """
        Remove old backup files, keeping only the most recent ones.
        
        Args:
            backup_dir: Directory containing backups
            file_stem: Base name of the file
        """
        try:
            # Get all backup files for this file
            backups = sorted(
                backup_dir.glob(f"{file_stem}_backup_*.xlsx"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            
            # Remove old backups
            for old_backup in backups[Config.MAX_BACKUP_FILES:]:
                old_backup.unlink()
                self.logger.debug(f"Removed old backup: {old_backup.name}")
                
        except Exception as e:
            self.logger.warning(f"Could not cleanup old backups: {e}")
    
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
            
            # Create backup of existing file
            if self.create_backup and file_path.exists():
                self._create_backup(file_path)
            
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
                self.logger.info(f"✅ Data saved successfully ({len(df)} total rows, {file_size:,} bytes)")
                return True
            else:
                self.logger.error("❌ File verification failed - file does not exist")
                return False
            
        except PermissionError:
            self.logger.error(f"❌ Permission denied: {file_path} (file may be open in another program)")
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
    
    def __init__(self, verbose: bool = False, create_backup: bool = True):
        """
        Initialize scraper with all components.
        
        Args:
            verbose: Enable verbose logging
            create_backup: Create backup files before overwriting
        """
        self.logger = LoggerSetup.setup_logger(verbose=verbose)
        self.processor = DataProcessor(self.logger)
        self.storage = DataStorage(self.logger, create_backup=create_backup)
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
        self.logger.info(f"   - Excluded symbols: {Config.REMOVE_SYMBOLS}")
        self.logger.info("=" * 70)
        
        # Use context manager for proper session cleanup
        with MarketDataFetcher(self.logger) as fetcher:
            try:
                # Fetch data
                response = fetcher.fetch_data()
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
def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="PSX Market Data Scraper - Single Execution Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python script.py                  # Normal execution
  python script.py --verbose        # Verbose logging
  python script.py --no-backup      # Don't create backup files
  python script.py --check-deps     # Check dependencies only
        """
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose (DEBUG level) logging'
    )
    
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='Disable backup file creation'
    )
    
    parser.add_argument(
        '--check-deps',
        action='store_true',
        help='Check dependencies and exit'
    )
    
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_arguments()
    
    # Check dependencies
    deps_ok, missing = DependencyChecker.check_dependencies()
    
    if not deps_ok:
        print("=" * 70)
        print("❌ MISSING DEPENDENCIES")
        print("=" * 70)
        print("The following packages are required but not installed:")
        for pkg in missing:
            print(f"  - {pkg}")
        print("\nInstall them using:")
        print(f"  pip install {' '.join(missing)}")
        print("=" * 70)
        sys.exit(1)
    
    if args.check_deps:
        print("✅ All dependencies are installed!")
        sys.exit(0)
    
    # Initial debug information
    print("=" * 70)
    print("PSX Market Data Collector - Single Execution Mode")
    print("=" * 70)
    print(f"Python Version: {sys.version}")
    print(f"Current Directory: {os.getcwd()}")
    print(f"Script Location: {os.path.abspath(__file__)}")
    print(f"Time: {datetime.now()}")
    print(f"Verbose Mode: {'ON' if args.verbose else 'OFF'}")
    print(f"Backup Creation: {'OFF' if args.no_backup else 'ON'}")
    print("=" * 70)
    print()
    
    try:
        scraper = MarketDataScraper(
            verbose=args.verbose,
            create_backup=not args.no_backup
        )
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
