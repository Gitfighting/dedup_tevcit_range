"""
Cost Logger for Python Simulation
Mirrors the Java CostLogger functionality for detailed cost tracking and debugging.
Follows the Open-Closed Principle - users can extend logging without modifying this class.
"""

import os
from datetime import datetime
from typing import Optional


class CostLogger:
    """
    Static logger class for tracking and logging detailed cost calculations.
    Provides formatted logs matching the Java implementation for easy comparison.
    """
    
    _writer: Optional[object] = None
    _log_dir: str = "logs"
    _timestamp_format: str = "%Y-%m-%d %H:%M:%S.%f"
    
    @staticmethod
    def initialize(tag: str, trace: str, config: str, set_base: bool, num_vms: int) -> None:
        """
        Initialize the cost logger with simulation configuration.
        
        Args:
            tag: Identifier tag for this simulation run
            trace: Path to trace dataset file
            config: Path to configuration file
            set_base: True for Fixed-Base (FB) mode, False for Moving-Base (MB)
            num_vms: Number of virtual machines
        """
        try:
            # Disable logging for T29 dataset to match Java behavior
            if trace and "T29" in trace:
                print("Cost logging disabled for T29 dataset.")
                return
            
            # Create logs directory if it doesn't exist
            if not os.path.exists(CostLogger._log_dir):
                os.makedirs(CostLogger._log_dir)
            
            # Create log file with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = f"cost_calculation_{tag}_{timestamp}.log"
            log_file_path = os.path.join(CostLogger._log_dir, log_filename)
            
            CostLogger._writer = open(log_file_path, 'a', encoding='utf-8')
            
            # Write header with detailed configuration
            separator = "=" * 100
            CostLogger._writer.write(separator + "\n")
            CostLogger._writer.write(f"Cost Calculation Log - Started at: {datetime.now().strftime(CostLogger._timestamp_format)[:-3]}\n")
            CostLogger._writer.write(separator + "\n\n")
            
            # Configuration details
            CostLogger._writer.write("CONFIGURATION:\n")
            CostLogger._writer.write(f"  Tag:            {tag}\n")
            CostLogger._writer.write(f"  Config File:    {config}\n")
            CostLogger._writer.write(f"  Trace Dataset:  {trace}\n")
            CostLogger._writer.write(f"  Mode:           {'Fixed-Base (FB)' if set_base else 'Moving-Base (MB)'}\n")
            CostLogger._writer.write(f"  Number of VMs:  {num_vms}\n\n")
            CostLogger._writer.write(separator + "\n\n")
            CostLogger._writer.flush()
            
            print(f"Cost logging initialized: {log_file_path}")
        except Exception as e:
            print(f"Failed to initialize cost logger: {e}")
    
    @staticmethod
    def log_storage_removal(region: str, obj_key: str, size_gb: float, 
                           duration_hours: float, cost: float) -> None:
        """
        Log storage cost when an object is removed from a region.
        
        Args:
            region: Region name where object was stored
            obj_key: Object key/identifier
            size_gb: Object size in GB
            duration_hours: Duration object was stored in hours
            cost: Storage cost in dollars
        """
        if CostLogger._writer is None:
            return
        
        try:
            timestamp = datetime.now().strftime(CostLogger._timestamp_format)[:-3]
            log_line = (
                f"[{timestamp}] [REMOVE-COST] Region={region}, Obj={obj_key}, "
                f"Size={size_gb} GB, Duration={duration_hours} hrs, Cost=${cost}\n"
            )
            CostLogger._writer.write(log_line)
            CostLogger._writer.flush()
        except Exception as e:
            print(f"Failed to write storage removal log: {e}")
    
    @staticmethod
    def log_storage_remaining(region: str, phys_count: int, branch: str,
                             ttl: int, size_gb: float, duration_hours: float, 
                             cost: float) -> None:
        """
        Log storage cost for objects remaining in storage at simulation end.
        
        Args:
            region: Region name where object is stored
            phys_count: Number of physical replicas
            branch: Branch identifier or object key
            ttl: Time-to-live value (-1 for permanent)
            size_gb: Object size in GB
            duration_hours: Duration object was stored in hours
            cost: Storage cost in dollars
        """
        if CostLogger._writer is None:
            return
        
        try:
            timestamp = datetime.now().strftime(CostLogger._timestamp_format)[:-3]
            log_line = (
                f"[{timestamp}] [REMAIN-COST] Region={region}, PhysCount={phys_count}, "
                f"Branch={branch}, TTL={ttl}, Size={size_gb} GB, "
                f"Duration={duration_hours} hrs, Cost=${cost}\n"
            )
            CostLogger._writer.write(log_line)
            CostLogger._writer.flush()
        except Exception as e:
            print(f"Failed to write storage remaining log: {e}")
    
    @staticmethod
    def log_network_cost(operation: str, obj_key: str, src: str, dst: str,
                        size_gb: float, cost: float) -> None:
        """
        Log network transfer cost for PUT/GET operations.
        
        Args:
            operation: Operation type (PUT or GET)
            obj_key: Object key/identifier
            src: Source region
            dst: Destination region
            size_gb: Transfer size in GB
            cost: Transfer cost in dollars
        """
        if CostLogger._writer is None:
            return
        
        try:
            timestamp = datetime.now().strftime(CostLogger._timestamp_format)[:-3]
            log_line = (
                f"[{timestamp}] [NET-COST-{operation.upper()}] Obj={obj_key}, "
                f"Src={src}, Dst={dst}, Size={size_gb} GB, TransferCost=${cost}\n"
            )
            CostLogger._writer.write(log_line)
            CostLogger._writer.flush()
        except Exception as e:
            print(f"Failed to write network cost log: {e}")
    
    @staticmethod
    def log_error(error_message: str, exception: Optional[Exception] = None) -> None:
        """
        Log error information when simulation fails.
        
        Args:
            error_message: Error description message
            exception: Optional exception object with stack trace
        """
        if CostLogger._writer is None:
            return
        
        try:
            separator = "=" * 100
            CostLogger._writer.write("\n" + separator + "\n")
            CostLogger._writer.write("ERROR OCCURRED - Simulation Failed\n")
            CostLogger._writer.write(f"Time: {datetime.now().strftime(CostLogger._timestamp_format)[:-3]}\n")
            CostLogger._writer.write(separator + "\n\n")
            CostLogger._writer.write(f"Error Message: {error_message}\n\n")
            
            if exception is not None:
                CostLogger._writer.write(f"Exception Type: {type(exception).__name__}\n")
                CostLogger._writer.write(f"Exception Message: {str(exception)}\n\n")
                
                import traceback
                CostLogger._writer.write("Stack Trace:\n")
                CostLogger._writer.write(traceback.format_exc())
            
            CostLogger._writer.write("\n" + separator + "\n")
            CostLogger._writer.flush()
            
            print("Error information written to cost log file")
        except Exception as e:
            print(f"Failed to write error to log: {e}")
    
    @staticmethod
    def close() -> None:
        """
        Close the cost logger and finalize the log file.
        """
        if CostLogger._writer is not None:
            try:
                separator = "=" * 100
                CostLogger._writer.write(separator + "\n")
                CostLogger._writer.write(f"Cost Calculation Log - Ended at: {datetime.now().strftime(CostLogger._timestamp_format)[:-3]}\n")
                CostLogger._writer.write(separator + "\n")
                CostLogger._writer.close()
                CostLogger._writer = None
                print("Cost logging closed.")
            except Exception as e:
                print(f"Failed to close cost logger: {e}")
