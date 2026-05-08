import os
import sys
import datetime
import atexit
import signal
import logging
import time

class LogManager:
    """
    Manages log files for the application with timestamps for start and end times.
    Creates a new log file for each run and renames it with end timestamp on exit.
    """
    def __init__(self, logs_dir='spider_logs'):

        self.logs_dir = logs_dir
        self.start_time = datetime.datetime.now()
        
        if not os.path.exists(self.logs_dir):
            os.makedirs(self.logs_dir)
            
        #unique log filename with start timestamp
        self.log_file_name = f"{self.logs_dir}/spider_log_{self.start_time.strftime('%Y%m%d_%H%M%S')}.txt"
        
        #log files header, checks for files creation as well
        with open(self.log_file_name, 'w') as f:
            f.write(f"=== Spider log started at {self.start_time} ===\n")
        
        #exit handlers
        atexit.register(self._on_exit)
        self._setup_signal_handlers()
        
        
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)  #capture all logs
        
        #remove all existing handlers
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        
        #file handler for DEBUG and above
        file_handler = logging.FileHandler(self.log_file_name)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
        
        #console handler for WARNING and above
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_formatter = logging.Formatter('%(levelname)s - %(message)s')
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
        
        logging.info(f"Log manager initialized. Logging to: {self.log_file_name}")
        logging.warning(f"WARNING and above logs will be displayed in terminal")
    
    def _on_exit(self):
        """
        Handler called when the application exits.
        Renames the log file to include end timestamp.
        """
        time.sleep(0.5)
        
        end_time = datetime.datetime.now()
        try:
            with open(self.log_file_name, 'a') as f:
                f.write(f"\n=== Spider log ended at {end_time} ===\n")
        except Exception as e:
            print(f"Error writing log footer: {e}")
            
        #renames the log file to include end time
        new_log_file_name = f"{self.logs_dir}/spider_log_{self.start_time.strftime('%Y%m%d_%H%M%S')}_to_{end_time.strftime('%Y%m%d_%H%M%S')}.txt"
        try:
            if os.path.exists(self.log_file_name):
                os.rename(self.log_file_name, new_log_file_name)
                print(f"Log file renamed to: {new_log_file_name}")
        except Exception as e:
            print(f"Error renaming log file: {e}")
    
    def _setup_signal_handlers(self):
        """
        Set up signal handlers for graceful shutdown
        """
        #custom handler that logs before exiting
        def handle_signal(signum, frame):
            logging.warning(f"Received signal {signum}, shutting down...")
            sys.exit(0)
            
        for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]:
            signal.signal(sig, handle_signal)
    
    def configure_scrapy_logging(self, settings):
        """
        Configure Scrapy settings for logging
        
        Args:
            settings: Scrapy settings object
        """
        #log file exists and is writable
        with open(self.log_file_name, 'a') as f:
            f.write("Configuring Scrapy logging...\n")
            
        #show warnings in terminal and debug in file
        settings.set('LOG_FILE', self.log_file_name)
        settings.set('LOG_FILE_LEVEL', 'DEBUG')  #DEBUG+ in log file
        settings.set('LOG_LEVEL', 'WARNING')  #WARNING+ in terminal
        settings.set('LOG_STDOUT', False)  #don't log stdout
        
        return settings

log_manager = LogManager()

def configure_scrapy_logging(settings):
    """
    Convenience function to configure Scrapy logging settings
    
    Args:
        settings: Scrapy settings object
    
    Returns:
        Updated settings object
    """
    return log_manager.configure_scrapy_logging(settings) 