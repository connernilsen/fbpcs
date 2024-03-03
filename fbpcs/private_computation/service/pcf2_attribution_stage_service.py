#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# pyre-strict


from typing import Any, DefaultDict, Dict, List, Optional

from fbpcp.util.typing import checked_cast
from fbpcs.onedocker_binary_config import OneDockerBinaryConfig
from fbpcs.onedocker_binary_names import OneDockerBinaryNames
from fbpcs.private_computation.entity.pcs_feature import PCSFeature
from fbpcs.private_computation.entity.private_computation_instance import (
    PrivateComputationInstance,
)
from fbpcs.private_computation.entity.product_config import (
    AttributionConfig,
    AttributionRule,
)
from fbpcs.private_computation.repository.private_computation_game import GameNames
from fbpcs.private_computation.service.argument_helper import get_tls_arguments
from fbpcs.private_computation.service.constants import DEFAULT_LOG_COST_TO_S3

from fbpcs.private_computation.service.mpc.mpc import MPCService
from fbpcs.private_computation.service.pcf2_base_stage_service import (
    PCF2BaseStageService,
)
from fbpcs.private_computation.service.private_computation_service_data import (
    PrivateComputationServiceData,
)
from fbpcs.private_computation.service.utils import stop_stage_service


class PCF2AttributionStageService(PCF2BaseStageService):
    """Handles business logic for the pcf2.0 based private attribution stage"""

    def __init__(
        self,
        onedocker_binary_config_map: DefaultDict[str, OneDockerBinaryConfig],
        mpc_service: MPCService,
        log_cost_to_s3: bool = DEFAULT_LOG_COST_TO_S3,
        container_timeout: Optional[int] = None,
    ) -> None:
        super().__init__(
            onedocker_binary_config_map=onedocker_binary_config_map,
            mpc_service=mpc_service,
            stage_data=PrivateComputationServiceData.PCF2_ATTRIBUTION_STAGE_DATA,
            instance_id_suffix="_pcf2_attribution",
            binary_name=OneDockerBinaryNames.PCF2_ATTRIBUTION.value,
            stage_name="Attribution",
            log_cost_to_s3=log_cost_to_s3,
            container_timeout=container_timeout,
        )

    # TODO T88759390: Make this function truly async. It is not because it calls blocking functions.
    # Make an async version of run_async() so that it can be called by Thrift

    def stop_service(
        self,
        pc_instance: PrivateComputationInstance,
    ) -> None:
        stop_stage_service(pc_instance, self._mpc_service.onedocker_svc)

    # For now, only passing the attribution game arguments, as this game is currently only used for PA.
    def get_game_args(
        self,
        private_computation_instance: PrivateComputationInstance,
        server_certificate_path: str,
        ca_certificate_path: str,
    ) -> List[Dict[str, Any]]:
        """Gets the game args passed to game binaries by onedocker

        When onedocker spins up containers to run games, it unpacks a dictionary containing the
        arguments required by the game binary being ran. This function prepares that dictionary.

        Args:
            pc_instance: the private computation instance to generate game args for.
            server_certificate_path: The path to write server certificate on a container.
            ca_certificate_path: The path to write CA certificate on a container.

        Returns:
            MPC game args to be used by onedocker
        """

        attribution_config: AttributionConfig = checked_cast(
            AttributionConfig,
            private_computation_instance.product_config,
        )
        attribution_rule: AttributionRule = attribution_config.attribution_rule

        run_name_base = f"{private_computation_instance.infra_config.instance_id}_{GameNames.PCF2_ATTRIBUTION.value}"

        tls_args = get_tls_arguments(
            private_computation_instance.infra_config.is_tls_enabled,
            server_certificate_path,
            ca_certificate_path,
        )

        cmd_args_list = []
        for shard in range(
            private_computation_instance.infra_config.num_mpc_containers
        ):
            run_name = f"{run_name_base}_{shard}" if self._log_cost_to_s3 else ""
            game_args: Dict[str, Any] = {
                "input_base_path": private_computation_instance.data_processing_output_path,
                "output_base_path": private_computation_instance.pcf2_attribution_stage_output_base_path,
                "file_start_index": shard
                * private_computation_instance.infra_config.num_files_per_mpc_container,
                "num_files": private_computation_instance.infra_config.num_files_per_mpc_container,
                "concurrency": private_computation_instance.infra_config.mpc_compute_concurrency,
                "run_name": run_name,
                "max_num_touchpoints": private_computation_instance.product_config.common.padding_size,
                "max_num_conversions": private_computation_instance.product_config.common.padding_size,
                "log_cost": self._log_cost_to_s3,
                "attribution_rules": attribution_rule.value,
                "use_xor_encryption": True,
                "use_postfix": True,
                "run_id": private_computation_instance.infra_config.run_id,
                "log_cost_s3_bucket": private_computation_instance.infra_config.log_cost_bucket,
                "use_new_output_format": private_computation_instance.has_feature(
                    PCSFeature.PRIVATE_ATTRIBUTION_REFORMATTED_OUTPUT
                ),
                **tls_args,
            }

            if private_computation_instance.feature_flags is not None:
                game_args["pc_feature_flags"] = (
                    private_computation_instance.feature_flags
                )

            if (
                self._log_cost_to_s3
                and private_computation_instance.product_config.common.post_processing_data
            ):
                private_computation_instance.product_config.common.post_processing_data.s3_cost_export_output_paths.add(
                    f"att-logs/{run_name}_{private_computation_instance.infra_config.role.value.title()}.json"
                )

            cmd_args_list.append(game_args)

        return cmd_args_list
